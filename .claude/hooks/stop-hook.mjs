#!/usr/bin/env node
/**
 * Decifer Trading — Stop Hook (Context Freshness Guard)
 *
 * Fires when Claude is about to stop responding. Checks whether code
 * changed this session without CLAUDE.md or memory files being updated.
 * If so, returns {"continue": true} to make Claude do a docs-update pass
 * before the session ends.
 *
 * Guard flag prevents infinite loops: the hook sets a flag on first fire,
 * so if Claude stops again after the docs pass, it is allowed through.
 *
 * The flag is also written to a pending-update file so the next session's
 * start hook can surface it as a fallback if this hook's continue is missed.
 */

import { readFileSync, writeFileSync, existsSync, unlinkSync } from 'fs';
import { join, resolve, dirname } from 'path';
import { execSync } from 'child_process';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const MEMORY_DIR = join(REPO_ROOT, '.claude', 'memory');
const GUARD_FLAG = join(MEMORY_DIR, 'stop-hook-guard.flag');
const PENDING_UPDATE_FILE = join(MEMORY_DIR, 'pending-doc-update.json');

// Patterns that indicate real code work happened
const CODE_PATTERNS = [/\.py$/, /requirements\.txt$/, /settings\.json$/];

// Patterns to ignore — runtime state, not meaningful changes
const IGNORE_PATTERNS = [
  /checkpoint\.json/,
  /session-history/,
  /signals_log/,
  /audit_log/,
  /trades\.json/,
  /seen-hashes/,
  /stop-hook/,
  /pending-doc/,
  /live_ic_report/,
  /orders\.json/,
];

function safeExec(cmd) {
  try {
    return execSync(cmd, { cwd: REPO_ROOT, encoding: 'utf-8' }).trim();
  } catch {
    return '';
  }
}

function safeRead(path) {
  try { return JSON.parse(readFileSync(path, 'utf-8')); } catch { return null; }
}

function isCodeFile(f) {
  return CODE_PATTERNS.some(p => p.test(f)) && !IGNORE_PATTERNS.some(p => p.test(f));
}

function isDocFile(f) {
  return f === 'CLAUDE.md' || f.startsWith('docs/') || f.startsWith('.claude/projects/');
}

try {
  // ── Guard: if we already triggered a continue this session, allow stop ──
  if (existsSync(GUARD_FLAG)) {
    const guardData = safeRead(GUARD_FLAG) || {};
    unlinkSync(GUARD_FLAG);

    // Clear the pending-update file since Claude should have handled it
    if (existsSync(PENDING_UPDATE_FILE)) {
      unlinkSync(PENDING_UPDATE_FILE);
    }

    process.stderr.write('[stop-hook] guard flag found — allowing stop after doc-update pass\n');
    process.exit(0);
  }

  // ── Collect changed files (working tree + staged + last commit) ──
  const statusOutput = safeExec('git status --short');
  const workingFiles = statusOutput.split('\n').filter(Boolean).map(l => l.slice(3).trim());

  // Also check what changed in the most recent commit relative to previous
  const commitDiffOutput = safeExec('git diff HEAD~1 --name-only 2>/dev/null');
  const commitFiles = commitDiffOutput.split('\n').filter(Boolean);

  const allChanged = [...new Set([...workingFiles, ...commitFiles])];

  const codeChanged = allChanged.filter(isCodeFile);
  const docsChanged = allChanged.some(isDocFile);

  if (codeChanged.length === 0) {
    // No code changes — docs don't need updating
    process.exit(0);
  }

  if (docsChanged) {
    // Docs were already updated this session — all good
    process.exit(0);
  }

  // ── Code changed but docs didn't — trigger a doc-update pass ──
  const changedList = codeChanged.slice(0, 8).join(', ');

  // Write guard flag to prevent infinite loop on second stop
  writeFileSync(GUARD_FLAG, JSON.stringify({
    firedAt: new Date().toISOString(),
    codeChanged: codeChanged,
  }), 'utf-8');

  // Write pending-update file as fallback for next session's start hook
  writeFileSync(PENDING_UPDATE_FILE, JSON.stringify({
    date: new Date().toISOString(),
    codeChanged: codeChanged,
    message: 'Code changed without docs update — CLAUDE.md and memory may be stale',
  }), 'utf-8');

  process.stderr.write(`[stop-hook] code changed without docs update — requesting continue\n`);

  // Return continue: true with the directive for Claude
  const directive = [
    'STOP HOOK: Code changed this session without updating context docs.',
    `Changed files: ${changedList}`,
    '',
    'Before stopping, do the following (takes 1-2 minutes):',
    '1. Update CLAUDE.md "Current State" section if the phase, features built, or gate conditions changed.',
    '2. Add any new locked architectural decisions to CLAUDE.md "Architectural Decisions" section.',
    '3. Update docs/DECISIONS.md with any new decisions made this session (date, decision, reasoning).',
    '4. Update the memory file at /Users/amitchopra/.claude/projects/-Users-amitchopra-Desktop-decifer-trading/memory/project_decifer.md if current phase or gates changed.',
    '',
    'If nothing significant changed in the architecture or phase, just confirm "no doc updates needed".',
    '',
    '─────────────────────────────────────────────────────',
    'SESSION SUMMARY — draft this for Amit to approve:',
    '─────────────────────────────────────────────────────',
    'DATE: [today]',
    '',
    'WHAT CHANGED:',
    '  - [file or feature]: [what was built/fixed and why]',
    '',
    'WHAT WAS DELETED:',
    '  - [file or function removed, or "nothing deleted"]',
    '',
    'DECISIONS MADE:',
    '  - [any locked architectural decision, or "none"]',
    '',
    'TESTS:',
    '  - [pass/fail count, or tests not applicable]',
    '',
    'WHAT IS NEXT:',
    '  - [next logical task, or "nothing — phase gate not met"]',
    '─────────────────────────────────────────────────────',
  ].join('\n');

  process.stdout.write(JSON.stringify({
    continue: true,
    reason: directive,
  }));

  process.exit(0);

} catch (err) {
  // Never crash Claude — silent failure, allow stop
  process.stderr.write(`[stop-hook] error: ${err.message}\n`);
  process.exit(0);
}
