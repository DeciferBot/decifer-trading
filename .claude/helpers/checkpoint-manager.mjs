#!/usr/bin/env node
/**
 * Decifer Trading — Checkpoint Manager (Phase 3)
 *
 * Saves and restores session state so every new session starts knowing:
 * - Which branch is active
 * - Which roadmap phase we're in
 * - What was left open
 * - Which files were last touched
 *
 * Usage:
 *   node .claude/helpers/checkpoint-manager.mjs save [options as JSON via stdin]
 *   node .claude/helpers/checkpoint-manager.mjs restore
 *   node .claude/helpers/checkpoint-manager.mjs status
 */

import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'fs';
import { join, resolve, dirname } from 'path';
import { execSync } from 'child_process';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const MEMORY_DIR = join(REPO_ROOT, '.claude', 'memory');
const CHECKPOINT_PATH = join(MEMORY_DIR, 'checkpoint.json');

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

function loadCheckpoint() {
  if (!existsSync(CHECKPOINT_PATH)) return null;
  try {
    return JSON.parse(readFileSync(CHECKPOINT_PATH, 'utf-8'));
  } catch {
    return null;
  }
}

function saveCheckpoint(data) {
  ensureMemoryDir();
  writeFileSync(CHECKPOINT_PATH, JSON.stringify(data, null, 2), 'utf-8');
}

// ─── Auto-detect git state ────────────────────────────────────────────────────
function detectGitState() {
  const branch = safeExec('git rev-parse --abbrev-ref HEAD');
  const lastCommit = safeExec('git log -1 --pretty=format:"%h %s"');
  const status = safeExec('git status --short');
  const changedFiles = status
    ? status.split('\n').filter(Boolean).map(l => l.slice(3).trim()).slice(0, 10)
    : [];

  return { branch, lastCommit, changedFiles };
}

// ─── Commands ─────────────────────────────────────────────────────────────────
function cmdSave() {
  let input = {};
  try {
    // Accept optional JSON from stdin for open todos, notes, etc.
    const raw = readFileSync('/dev/stdin', 'utf-8');
    if (raw.trim()) input = JSON.parse(raw);
  } catch {
    // stdin not available or empty — use auto-detection only
  }

  const git = detectGitState();

  // Detect roadmap phase from branch name
  let roadmapPhase = input.roadmapPhase || null;
  if (!roadmapPhase && git.branch) {
    const phaseMatch = git.branch.match(/phase[-_]([a-e])/i);
    if (phaseMatch) roadmapPhase = phaseMatch[1].toUpperCase();
  }

  const checkpoint = {
    timestamp: new Date().toISOString(),
    branch: git.branch || input.branch || 'unknown',
    lastCommit: git.lastCommit || null,
    roadmapPhase: roadmapPhase,
    lastFilesTouched: input.lastFilesTouched || git.changedFiles || [],
    openTodos: input.openTodos || [],
    notes: input.notes || null,
    sessionCount: (loadCheckpoint()?.sessionCount || 0) + 1,
  };

  saveCheckpoint(checkpoint);

  console.log(JSON.stringify({
    status: 'saved',
    checkpoint,
  }));
}

function cmdRestore() {
  const checkpoint = loadCheckpoint();
  if (!checkpoint) {
    console.log(JSON.stringify({ status: 'no_checkpoint', message: 'No checkpoint found. Starting fresh.' }));
    return;
  }
  console.log(JSON.stringify({ status: 'restored', checkpoint }));
}

function cmdStatus() {
  const checkpoint = loadCheckpoint();
  if (!checkpoint) {
    console.log('No checkpoint saved yet.');
    return;
  }

  const age = Math.round((Date.now() - new Date(checkpoint.timestamp).getTime()) / 1000 / 60);
  console.log(`
╔══════════════════════════════════════════╗
║         Decifer Checkpoint Status        ║
╚══════════════════════════════════════════╝
  Saved:       ${checkpoint.timestamp} (${age} min ago)
  Branch:      ${checkpoint.branch}
  Phase:       ${checkpoint.roadmapPhase || 'unknown'}
  Last commit: ${checkpoint.lastCommit || 'unknown'}
  Sessions:    ${checkpoint.sessionCount}
  Open todos:  ${checkpoint.openTodos?.length ? checkpoint.openTodos.join(', ') : 'none'}
  Last files:  ${checkpoint.lastFilesTouched?.length ? checkpoint.lastFilesTouched.join(', ') : 'none'}
  Notes:       ${checkpoint.notes || 'none'}
`);
}

// ─── Entry point ──────────────────────────────────────────────────────────────
const cmd = process.argv[2] || 'status';

switch (cmd) {
  case 'save':    cmdSave();    break;
  case 'restore': cmdRestore(); break;
  case 'status':  cmdStatus();  break;
  default:
    console.error(`Unknown command: ${cmd}. Use save | restore | status`);
    process.exit(1);
}
