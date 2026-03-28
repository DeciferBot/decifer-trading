#!/usr/bin/env node
/**
 * Decifer Trading — Session End Hook (Phase 3)
 *
 * Runs when a Claude session ends. Saves a checkpoint capturing current
 * git state so the next session starts oriented. Also updates the seen-hashes
 * store used by the session-start hook for deduplication.
 *
 * Claude Code passes session data as JSON via stdin.
 */

import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'fs';
import { join, resolve, dirname } from 'path';
import { execSync } from 'child_process';
import { createHash } from 'crypto';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const MEMORY_DIR = join(REPO_ROOT, '.claude', 'memory');
const CHECKPOINT_PATH = join(MEMORY_DIR, 'checkpoint.json');
const SEEN_HASHES_PATH = join(MEMORY_DIR, 'seen-hashes.json');
const SESSION_LOG_PATH = join(MEMORY_DIR, 'session-history.jsonl');

const MAX_SEEN_HASHES = 50; // rolling dedup window

// ─── Helpers ──────────────────────────────────────────────────────────────────
function ensureMemoryDir() {
  if (!existsSync(MEMORY_DIR)) {
    mkdirSync(MEMORY_DIR, { recursive: true });
  }
}

function safeExec(cmd) {
  try {
    return execSync(cmd, { cwd: REPO_ROOT, encoding: 'utf-8' }).trim();
  } catch {
    return null;
  }
}

function safeRead(path) {
  try { return JSON.parse(readFileSync(path, 'utf-8')); } catch { return null; }
}

function hash(str) {
  return createHash('sha256').update(str).digest('hex').slice(0, 16);
}

// ─── Read session data from stdin ─────────────────────────────────────────────
function readStdin() {
  try {
    const raw = readFileSync('/dev/stdin', 'utf-8');
    return raw.trim() ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

// ─── Save checkpoint ──────────────────────────────────────────────────────────
function saveCheckpoint(sessionData) {
  const branch = safeExec('git rev-parse --abbrev-ref HEAD');
  const lastCommit = safeExec('git log -1 --pretty=format:"%h %s"');
  const statusRaw = safeExec('git status --short');
  const changedFiles = statusRaw
    ? statusRaw.split('\n').filter(Boolean).map(l => l.slice(3).trim()).slice(0, 10)
    : [];

  // Extract roadmap phase from branch name
  let roadmapPhase = null;
  if (branch) {
    const phaseMatch = branch.match(/phase[-_]([a-e])/i);
    if (phaseMatch) roadmapPhase = phaseMatch[1].toUpperCase();
  }

  const existing = safeRead(CHECKPOINT_PATH) || {};

  const checkpoint = {
    timestamp: new Date().toISOString(),
    branch: branch || existing.branch || 'unknown',
    lastCommit: lastCommit || null,
    roadmapPhase: roadmapPhase || existing.roadmapPhase || null,
    lastFilesTouched: changedFiles.length ? changedFiles : (existing.lastFilesTouched || []),
    openTodos: sessionData.openTodos || existing.openTodos || [],
    notes: sessionData.notes || null,
    sessionCount: (existing.sessionCount || 0) + 1,
  };

  writeFileSync(CHECKPOINT_PATH, JSON.stringify(checkpoint, null, 2), 'utf-8');
  return checkpoint;
}

// ─── Update seen hashes (deduplication rolling window) ────────────────────────
function updateSeenHashes(contextHash) {
  if (!contextHash) return;
  const store = safeRead(SEEN_HASHES_PATH) || { hashes: [] };
  // Add new hash, keep rolling window
  store.hashes = [contextHash, ...store.hashes].slice(0, MAX_SEEN_HASHES);
  writeFileSync(SEEN_HASHES_PATH, JSON.stringify(store, null, 2), 'utf-8');
}

// ─── Append to session history log ────────────────────────────────────────────
function appendSessionHistory(checkpoint, sessionData) {
  const entry = {
    timestamp: checkpoint.timestamp,
    branch: checkpoint.branch,
    phase: checkpoint.roadmapPhase,
    sessionCount: checkpoint.sessionCount,
    summary: sessionData.summary || null,
    filesChanged: checkpoint.lastFilesTouched,
  };

  // JSONL format — one record per line
  const line = JSON.stringify(entry) + '\n';
  writeFileSync(SESSION_LOG_PATH, line, { flag: 'a', encoding: 'utf-8' });
}

// ─── Main ─────────────────────────────────────────────────────────────────────
try {
  ensureMemoryDir();

  const sessionData = readStdin();
  const checkpoint = saveCheckpoint(sessionData);

  // Record the context hash we used this session so next session can deduplicate
  if (sessionData.contextHash) {
    updateSeenHashes(sessionData.contextHash);
  }

  appendSessionHistory(checkpoint, sessionData);

  process.stderr.write(`[session-end-hook] Checkpoint saved. Branch: ${checkpoint.branch}, Phase: ${checkpoint.roadmapPhase || 'unknown'}, Session #${checkpoint.sessionCount}\n`);
} catch (err) {
  // Never crash Claude — silent failure
  process.stderr.write(`[session-end-hook] error: ${err.message}\n`);
  process.exit(0);
}
