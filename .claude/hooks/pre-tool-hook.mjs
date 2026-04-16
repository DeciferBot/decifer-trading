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

// ─── Architecture decision gate ──────────────────────────────────────────────
// These files are locked architectural areas. Any edit requires an explicit
// statement of DECISION, RISK, ARCHITECTURE FIT, and AMIT AWARENESS.
const ARCHITECTURE_GATE_PATHS = [
  { pattern: /\/signals\/__init__\.py$|\/signals\.py$/,  area: 'Signal Engine (10-dimension scoring — orthogonality rule applies)' },
  { pattern: /\/orders_core\.py$/,                       area: 'Order Submission Pipeline (trace full path before touching)' },
  { pattern: /\/config\.py$/,                            area: 'Config / Thresholds (live vs paper params — inline comments are the live values)' },
  { pattern: /\/learning\.py$/,                          area: 'IC Scoring / Learning Engine' },
  { pattern: /\/catalyst_engine\.py$/,                   area: 'Catalyst Screener' },
  { pattern: /\/bot_ibkr\.py$|\/bot\.py$/,               area: 'Main Bot Loop' },
  { pattern: /\/regime.*\.py$/,                          area: 'Regime Detection (VIX-proxy LOCKED — HMM deferred until ≥200 trades)' },
  { pattern: /\/universe.*\.py$/,                        area: 'Three-Tier Universe Engine' },
  { pattern: /\/portfolio_manager\.py$|\/pm\.py$/,       area: 'Portfolio Manager / Position Sizing' },
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

    // Architecture decision gate — locked files require explicit justification
    const archGate = ARCHITECTURE_GATE_PATHS.find(r => r.pattern.test(path));
    if (archGate) {
      return {
        warn: true,
        warning: [
          `ARCHITECTURE GATE — editing locked area: ${archGate.area}`,
          `File: ${path}`,
          ``,
          `Before proceeding, state all four:`,
          `  DECISION: What specifically is changing in this file, and why`,
          `  RISK:     What could break or have unintended effects on the signal/order pipeline`,
          `  FIT:      Confirm this does not violate any locked decision in CLAUDE.md`,
          `  SCOPE:    Is this Tier 1 (read/check), Tier 2 (implement), or Tier 3 (multi-file refactor)?`,
          `            Tier 3 requires Amit approval of approach BEFORE any code.`,
          ``,
          `If you cannot answer all four, stop and ask Amit first.`,
        ].join('\n'),
      };
    }

    // Plan gate: read-before-write + scope/risk declaration for all Python files
    if (/\.py$/.test(path)) {
      return {
        warn: true,
        warning: [
          `PLAN GATE — editing ${path}`,
          `Before this edit, confirm you have stated:`,
          `  READ:  "I read [file] lines [X–Y] — it currently does [...]"`,
          `  SCOPE: What specifically is changing and why`,
          `  RISK:  What could break as a result of this change`,
          `If you have not done this, stop and read the file first.`,
        ].join('\n'),
      };
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

  // Block worktree creation — this project pushes directly to master (CLAUDE.md)
  if (toolName === 'EnterWorktree') {
    return { block: true, reason: 'Worktrees are disabled for this project. Push directly to master per CLAUDE.md protocol.' };
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
