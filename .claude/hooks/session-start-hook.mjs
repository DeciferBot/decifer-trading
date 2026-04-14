#!/usr/bin/env node
/**
 * Decifer Trading — Session Start Hook (Phase 1: Persistent Context)
 *
 * Runs at the start of every Claude session. Reads state files from the
 * Chief Decifer data contracts and injects a compact context summary so
 * Claude starts every session already oriented — no re-explaining needed.
 *
 * Outputs JSON with { additionalContext: "..." } which Claude Code injects
 * before the first user prompt.
 *
 * Adapted from Ruflo context-persistence-hook.mjs patterns.
 */

import { readFileSync, existsSync, readdirSync, statSync } from 'fs';
import { join, resolve, dirname } from 'path';
import { createHash } from 'crypto';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));

// ─── Configuration ────────────────────────────────────────────────────────────
// Paths relative to the decifer-trading repo root
const REPO_ROOT = resolve(__dirname, '..', '..');
const CHIEF_STATE = process.env.CHIEF_STATE_PATH
  || resolve(REPO_ROOT, 'chief-decifer', 'state');

const PATHS = {
  sessions:      join(CHIEF_STATE, 'sessions'),
  research:      join(CHIEF_STATE, 'research'),
  specs:         join(CHIEF_STATE, 'specs'),
  backlog:       join(CHIEF_STATE, 'backlog.json'),
  checkpoint:    join(REPO_ROOT, '.claude', 'memory', 'checkpoint.json'),
  seenHashes:    join(REPO_ROOT, '.claude', 'memory', 'seen-hashes.json'),
  pendingUpdate: join(REPO_ROOT, '.claude', 'memory', 'pending-doc-update.json'),
  claudeMd:      join(REPO_ROOT, 'CLAUDE.md'),
};

const CONTEXT_BUDGET = 5000; // max chars injected into session

// ─── Helpers ──────────────────────────────────────────────────────────────────
function safeRead(path) {
  try {
    return JSON.parse(readFileSync(path, 'utf-8'));
  } catch {
    return null;
  }
}

function hash(str) {
  return createHash('sha256').update(str).digest('hex').slice(0, 16);
}

function listJsonFiles(dir) {
  if (!existsSync(dir)) return [];
  return readdirSync(dir)
    .filter(f => f.endsWith('.json'))
    .sort()
    .reverse(); // newest first (date-prefixed filenames)
}

function truncate(str, max) {
  if (!str) return '';
  return str.length > max ? str.slice(0, max) + '…' : str;
}

// ─── Load seen hashes (deduplication) ─────────────────────────────────────────
function loadSeenHashes() {
  return safeRead(PATHS.seenHashes) || { hashes: [] };
}

// ─── Check for pending doc updates from previous session ──────────────────────
function buildPendingUpdateSection() {
  const pending = safeRead(PATHS.pendingUpdate);
  if (!pending) return '';

  const lines = [
    '## ⚠ PENDING: Context Docs Need Updating',
    `The previous session changed code without updating CLAUDE.md or memory files.`,
    `Changed: ${(pending.codeChanged || []).slice(0, 6).join(', ')}`,
    '',
    'Do this FIRST before anything else:',
    '1. Update CLAUDE.md "Current State" section if phase/features changed.',
    '2. Add new decisions to CLAUDE.md "Architectural Decisions" + docs/DECISIONS.md.',
    '3. Update memory/project_decifer.md if phase or gates changed.',
    '(Then delete .claude/memory/pending-doc-update.json to clear this warning.)',
  ];
  return lines.join('\n');
}

// ─── Check CLAUDE.md freshness vs recent git activity ─────────────────────────
function buildFreshnessWarning() {
  try {
    // Get CLAUDE.md last modified time
    const claudeStat = statSync(PATHS.claudeMd);
    const claudeAge = Date.now() - claudeStat.mtimeMs;
    const claudeAgeDays = claudeAge / (1000 * 60 * 60 * 24);

    // Get most recent session log time
    const sessionFiles = existsSync(PATHS.sessions) ? readdirSync(PATHS.sessions).sort().reverse() : [];
    if (!sessionFiles.length) return '';

    const lastSessionFile = sessionFiles[0];
    const lastSessionDate = lastSessionFile.slice(0, 10); // YYYY-MM-DD prefix
    const lastSessionAge = (Date.now() - new Date(lastSessionDate).getTime()) / (1000 * 60 * 60 * 24);

    // Warn if CLAUDE.md is significantly older than the last session
    if (claudeAgeDays > lastSessionAge + 3) {
      return `## ⚠ CLAUDE.md May Be Stale\nLast updated ${Math.round(claudeAgeDays)} days ago, but sessions have run since. Verify "Current State" is accurate before starting work.`;
    }
    return '';
  } catch {
    return '';
  }
}

// ─── Context builders ─────────────────────────────────────────────────────────
function buildCheckpointSection(checkpoint) {
  if (!checkpoint) return '';
  const lines = [
    '## Active Checkpoint',
    `Branch: ${checkpoint.branch || 'unknown'}`,
    `Phase: ${checkpoint.roadmapPhase || 'unknown'}`,
    `Last session: ${checkpoint.timestamp ? new Date(checkpoint.timestamp).toLocaleDateString() : 'unknown'}`,
  ];
  if (checkpoint.openTodos?.length) {
    lines.push(`Open todos: ${checkpoint.openTodos.join(', ')}`);
  }
  if (checkpoint.lastFilesTouched?.length) {
    lines.push(`Last files touched: ${checkpoint.lastFilesTouched.join(', ')}`);
  }
  if (checkpoint.notes) {
    lines.push(`Notes: ${checkpoint.notes}`);
  }
  return lines.join('\n');
}

function buildSessionsSection(sessionsDir) {
  const files = listJsonFiles(sessionsDir).slice(0, 2); // last 2 sessions
  if (!files.length) return '';

  const lines = ['## Last Sessions'];
  for (const file of files) {
    const data = safeRead(join(sessionsDir, file));
    if (!data) continue;
    lines.push(`\n### ${data.date || file} — ${data.topic || 'session'}`);
    if (data.summary) lines.push(truncate(data.summary, 300));
    if (data.completed?.length) lines.push(`Completed: ${data.completed.join(', ')}`);
    if (data.next_steps?.length) lines.push(`Next steps: ${data.next_steps.slice(0, 3).join(', ')}`);
    if (data.branch) lines.push(`Branch: ${data.branch}`);
  }
  return lines.join('\n');
}

function buildActiveSpecsSection(specsDir) {
  const files = listJsonFiles(specsDir);
  const active = [];

  for (const file of files) {
    const spec = safeRead(join(specsDir, file));
    if (!spec) continue;
    if (['in_progress', 'pending', 'review'].includes(spec.status)) {
      active.push(spec);
    }
  }

  if (!active.length) return '## Active Specs\nNone currently in progress.';

  const lines = ['## Active Specs'];
  for (const spec of active.slice(0, 5)) {
    lines.push(`\n### [${spec.status.toUpperCase()}] ${spec.title} (${spec.id})`);
    lines.push(`Phase: ${spec.phase} | Priority: ${spec.priority}`);
    if (spec.summary) lines.push(truncate(spec.summary, 200));
    if (spec.approach) lines.push(`Approach: ${truncate(spec.approach, 150)}`);
    if (spec.files_affected?.length) lines.push(`Files: ${spec.files_affected.join(', ')}`);
    if (spec.branch) lines.push(`Branch: ${spec.branch}`);
  }
  return lines.join('\n');
}

function buildResearchSection(researchDir) {
  const files = listJsonFiles(researchDir).slice(0, 1); // most recent only
  if (!files.length) return '';

  const lines = ['## Latest Research'];
  for (const file of files) {
    const data = safeRead(join(researchDir, file));
    if (!data) continue;
    lines.push(`\n### ${data.date || file} — ${data.topic || 'research'}`);
    if (data.status) lines.push(`Status: ${data.status}`);
    if (data.findings?.length) {
      const topFindings = data.findings.slice(0, 3);
      for (const f of topFindings) {
        lines.push(`- ${f.feature}: ${truncate(f.summary, 120)} (${f.module})`);
      }
      if (data.findings.length > 3) {
        lines.push(`  ...and ${data.findings.length - 3} more findings`);
      }
    }
  }
  return lines.join('\n');
}

function buildBacklogSection(backlogPath) {
  const backlog = safeRead(backlogPath);
  if (!Array.isArray(backlog)) return '';

  const pending = backlog.filter(b => b.status === 'backlog' || b.status === 'pending');
  if (!pending.length) return '';

  const p1 = pending.filter(b => b.priority === 'P1').slice(0, 3);
  if (!p1.length) return '';

  const lines = ['## Top Backlog (P1)'];
  for (const item of p1) {
    lines.push(`- [${item.phase}] ${item.title} (${item.id}): ${truncate(item.summary, 100)}`);
  }
  return lines.join('\n');
}

// ─── Main ─────────────────────────────────────────────────────────────────────
function buildContext() {
  const sections = [];

  // 0. Pending doc update warning (highest priority — must be addressed first)
  const pendingSection = buildPendingUpdateSection();
  if (pendingSection) sections.push(pendingSection);

  // 1. Checkpoint (tells us exactly where we left off)
  const checkpoint = safeRead(PATHS.checkpoint);
  const checkpointSection = buildCheckpointSection(checkpoint);
  if (checkpointSection) sections.push(checkpointSection);

  // 2. Active specs (what are we building right now)
  const specsSection = buildActiveSpecsSection(PATHS.specs);
  if (specsSection) sections.push(specsSection);

  // 3. Last sessions (what happened recently)
  const sessionsSection = buildSessionsSection(PATHS.sessions);
  if (sessionsSection) sections.push(sessionsSection);

  // 4. Latest research (what did we learn)
  const researchSection = buildResearchSection(PATHS.research);
  if (researchSection) sections.push(researchSection);

  // 5. Backlog P1 (what's coming next)
  const backlogSection = buildBacklogSection(PATHS.backlog);
  if (backlogSection) sections.push(backlogSection);

  const raw = sections.join('\n\n');

  // Deduplicate using content hash — skip if identical to last session's context
  const seen = loadSeenHashes();
  const contextHash = hash(raw);
  const isDuplicate = seen.hashes.includes(contextHash);

  // Budget-constrained output
  const contextText = raw.length > CONTEXT_BUDGET
    ? raw.slice(0, CONTEXT_BUDGET) + '\n\n[...context truncated to budget]'
    : raw;

  return { contextText, contextHash, isDuplicate };
}

// ─── Entry point ──────────────────────────────────────────────────────────────
try {
  const { contextText, contextHash, isDuplicate } = buildContext();

  if (!contextText.trim()) {
    // No state files found yet — silent exit
    process.exit(0);
  }

  const header = [
    '# Decifer Trading — Session Context',
    `*Loaded at session start | Hash: ${contextHash}${isDuplicate ? ' (unchanged since last session)' : ''}*`,
    '',
  ].join('\n');

  const output = {
    additionalContext: header + contextText,
  };

  process.stdout.write(JSON.stringify(output));
} catch (err) {
  // Never crash Claude — silent failure
  process.stderr.write(`[session-start-hook] error: ${err.message}\n`);
  process.exit(0);
}
