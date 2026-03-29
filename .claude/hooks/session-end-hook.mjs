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

// Chief Decifer state path — absolute so it never breaks regardless of CWD
const CHIEF_STATE_PATH = process.env.CHIEF_STATE_PATH
  || '/Users/amitchopra/Documents/Claude/Projects/Chief Designer/Chief-Decifer/state';
const CHIEF_SESSIONS_DIR = join(CHIEF_STATE_PATH, 'sessions');

const MAX_SEEN_HASHES = 50; // rolling dedup window

// ─── Helpers ──────────────────────────────────────────────────────────────────
function ensureMemoryDir() {
  if (!existsSync(MEMORY_DIR)) {
    mkdirSync(MEMORY_DIR, { recursive: true });
  }
}

function ensureDir(dir) {
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
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

// ─── Write session log to Chief Decifer's state/sessions/ ─────────────────────
// Chief's analyse.py does: sorted(SESSIONS_DIR.glob("*.json")) — must be .json
// Format: YYYY-MM-DD_<slug>.json
function writeChiefSessionLog(checkpoint, sessionData) {
  if (!existsSync(CHIEF_STATE_PATH)) {
    process.stderr.write(`[session-end-hook] Chief state path not found: ${CHIEF_STATE_PATH} — skipping\n`);
    return;
  }

  ensureDir(CHIEF_SESSIONS_DIR);

  const today = new Date().toISOString().slice(0, 10);
  const commitMsg = checkpoint.lastCommit || '';
  const slug = (commitMsg.split(' ').slice(1, 4).join('-') || checkpoint.branch || 'session')
    .toLowerCase()
    .replace(/[^a-z0-9-]/g, '-')
    .replace(/-+/g, '-')
    .slice(0, 40);

  const filename = `${today}_${slug}.json`;
  const filepath = join(CHIEF_SESSIONS_DIR, filename);

  const testMatch = commitMsg.match(/(\d+)\s+passed/);

  const sessionLog = {
    date: today,
    timestamp: checkpoint.timestamp,
    branch: checkpoint.branch,
    phase: checkpoint.roadmapPhase || 'unknown',
    last_commit: checkpoint.lastCommit,
    files_changed: checkpoint.lastFilesTouched,
    test_status: testMatch ? `${testMatch[1]} passed` : 'unknown',
    summary: sessionData.summary || commitMsg || 'Session ended',
    open_todos: checkpoint.openTodos || [],
    session_count: checkpoint.sessionCount,
  };

  writeFileSync(filepath, JSON.stringify(sessionLog, null, 2), 'utf-8');
  process.stderr.write(`[session-end-hook] Chief session log written: ${filename}\n`);
}

// ─── Main ─────────────────────────────────────────────────────────────────────
try {
  ensureMemoryDir();

  const sessionData = readStdin();
  const checkpoint = saveCheckpoint(sessionData);

  if (sessionData.contextHash) {
    updateSeenHashes(sessionData.contextHash);
  }

  appendSessionHistory(checkpoint, sessionData);
  writeChiefSessionLog(checkpoint, sessionData);

  process.stderr.write(`[session-end-hook] Checkpoint saved. Branch: ${checkpoint.branch}, Phase: ${checkpoint.roadmapPhase || 'unknown'}, Session #${checkpoint.sessionCount}\n`);
} catch (err) {
  // Never crash Claude — silent failure
  process.stderr.write(`[session-end-hook] error: ${err.message}\n`);
  process.exit(0);
}
