#!/usr/bin/env node
/**
 * Decifer Trading — Pre-Tool Hook (Safety Gate)
 *
 * Runs before every tool execution. Blocks or warns on:
 * - Destructive bash commands (force push, hard reset, rm -rf)
 * - Writes to Chief Decifer dashboard code (non-state paths)
 * - Direct commits to main
 * - Reads of secret/credential files
 *
 * Claude Code sends hook data as JSON via stdin:
 *   { tool_name, tool_input: { command, file_path, ... } }
 *
 * Returns:
 *   Exit 0 + no output = allow
 *   Exit 0 + JSON { continue: false, reason } = block with message
 *   Exit 2 = hard block (emergency)
 */

import { readFileSync } from 'fs';

// ─── Read hook data from stdin ────────────────────────────────────────────────
function readHookData() {
  try {
    const raw = readFileSync('/dev/stdin', 'utf-8');
    return raw.trim() ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

// ─── Bash command safety rules ────────────────────────────────────────────────
const BLOCKED_COMMANDS = [
  { pattern: /git\s+push\s+.*--force/i,   reason: 'Force push blocked. Use --force-with-lease if essential, and only with Amit approval.' },
  { pattern: /git\s+reset\s+--hard/i,     reason: 'Hard reset blocked. Confirm with Amit — this discards uncommitted work.' },
  { pattern: /git\s+clean\s+-f/i,         reason: 'git clean -f blocked. This deletes untracked files irreversibly.' },
  { pattern: /git\s+checkout\s+--\s/i,    reason: 'git checkout -- blocked. Discards working directory changes.' },
  { pattern: /git\s+push.*main/i,         reason: 'Direct push to main blocked. Use a feat/ or fix/ branch and PR.' },
  { pattern: /rm\s+-rf?\s+\//i,           reason: 'rm -rf / blocked. This is a system-destroying command.' },
  { pattern: /rm\s+-rf?\s+\./i,           reason: 'rm -rf . blocked. This deletes the entire working directory.' },
  { pattern: /:\s*>\s*\S+\.py\b/,         reason: 'File truncation blocked. Use Edit tool to modify Python files.' },
  { pattern: /truncate\s+--size\s+0/i,    reason: 'File truncation blocked.' },
  { pattern: /drop\s+table/i,             reason: 'DROP TABLE blocked. Destructive DB operation requires Amit approval.' },
];

const WARNED_COMMANDS = [
  { pattern: /git\s+commit\s+.*main/i,  warning: 'Warning: committing on main branch — should be on feat/ or fix/ branch' },
  { pattern: /pip\s+install/i,          warning: 'Installing package — ensure it is compatible with the free data stack (yfinance, TA-Lib, etc.)' },
  { pattern: /chmod\s+777/i,            warning: 'chmod 777 is overly permissive — use 755 or 644' },
];

// ─── File path safety rules ───────────────────────────────────────────────────
const BLOCKED_WRITE_PATHS = [
  { pattern: /\.env($|\.)/i,                     reason: 'Writing .env files blocked — never commit secrets.' },
  { pattern: /chief.decifer\/app\.py/i,           reason: 'Blocked: chief-decifer/app.py is dashboard code, not a state file. Write to state/ paths only.' },
  { pattern: /chief.decifer\/panels\//i,          reason: 'Blocked: Chief Decifer panels are read-only display code. Write to state/ paths instead.' },
  { pattern: /chief.decifer\/config\.py/i,        reason: 'Blocked: Chief Decifer config is read-only. Coordinate with Amit to change dashboard config.' },
  { pattern: /\.claude\/memory\/checkpoint\.json/, reason: null }, // allow — checkpoint writes are expected
];

const BLOCKED_READ_PATHS = [
  { pattern: /\.env($|\.)/i,  reason: 'Reading .env blocked — credentials must not enter the context window.' },
  { pattern: /id_rsa|id_ed25519|\.pem$|\.key$/i, reason: 'Reading private keys blocked.' },
];

// ─── Evaluate ─────────────────────────────────────────────────────────────────
function evaluate(hookData) {
  const toolName = hookData.tool_name || '';
  const input = hookData.tool_input || {};

  // Check bash commands
  if (toolName === 'Bash' && input.command) {
    const cmd = input.command;

    for (const rule of BLOCKED_COMMANDS) {
      if (rule.pattern.test(cmd)) {
        return { block: true, reason: rule.reason };
      }
    }

    for (const rule of WARNED_COMMANDS) {
      if (rule.pattern.test(cmd)) {
        return { warn: true, warning: rule.warning };
      }
    }
  }

  // Check file writes
  if (['Write', 'Edit'].includes(toolName) && input.file_path) {
    const path = input.file_path;

    for (const rule of BLOCKED_WRITE_PATHS) {
      if (rule.pattern.test(path)) {
        if (rule.reason === null) continue; // explicitly allowed
        return { block: true, reason: rule.reason };
      }
    }
  }

  // Check file reads
  if (toolName === 'Read' && input.file_path) {
    const path = input.file_path;

    for (const rule of BLOCKED_READ_PATHS) {
      if (rule.pattern.test(path)) {
        return { block: true, reason: rule.reason };
      }
    }
  }

  return { allow: true };
}

// ─── Main ─────────────────────────────────────────────────────────────────────
try {
  const hookData = readHookData();
  const result = evaluate(hookData);

  if (result.block) {
    // Return a blocking response — Claude Code will show this to the user
    process.stdout.write(JSON.stringify({
      continue: false,
      reason: `[Decifer Safety Gate] ${result.reason}`,
    }));
    process.exit(0);
  }

  if (result.warn) {
    // Non-blocking warning — logged to stderr, visible in Claude Code output
    process.stderr.write(`[Decifer Safety Gate] ⚠  ${result.warning}\n`);
    process.exit(0);
  }

  // Allowed — silent exit
  process.exit(0);
} catch (err) {
  // Never crash Claude on hook errors
  process.stderr.write(`[pre-tool-hook] error: ${err.message}\n`);
  process.exit(0);
}
