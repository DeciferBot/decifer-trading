#!/usr/bin/env node
/**
 * Decifer Trading — Task Router (Phase 4)
 *
 * Classifies a task description into a complexity tier and recommends
 * an appropriate approach. Prevents burning deep reasoning budget on
 * simple tasks, and ensures architecture-level decisions get proper depth.
 *
 * Usage:
 *   echo "check if signals.py has thread safety issues" | node .claude/helpers/task-router.mjs
 *   node .claude/helpers/task-router.mjs "implement multi-timeframe signal confirmation"
 *
 * Returns JSON: { tier, label, approach, estimatedComplexity, warnings }
 */

// ─── Tier definitions ────────────────────────────────────────────────────────
const TIERS = {
  1: {
    label: 'Fast',
    description: 'Read, check, scan, pull data — no code changes',
    approach: 'Execute directly. Batch all reads in one pass. Report findings concisely.',
    maxRoundTrips: 1,
  },
  2: {
    label: 'Standard',
    description: 'Single-module change, bug fix, spec/log writing',
    approach: 'Read all relevant files first (batch). Implement. Test. Draft summary for Amit.',
    maxRoundTrips: 3,
  },
  3: {
    label: 'Deep',
    description: 'Multi-file refactor, architecture decision, bias analysis, new phase',
    approach: 'Present approach to Amit BEFORE writing code. Get explicit approval. Then batch-implement.',
    maxRoundTrips: null, // as needed
    requiresApproval: true,
  },
};

// ─── Pattern matching rules ───────────────────────────────────────────────────
const TIER_PATTERNS = [
  // Tier 1 — read/check/scan/data
  { tier: 1, patterns: [
    /\b(read|check|look at|show|display|print|list|scan|find|search|grep|view)\b/i,
    /\b(what is|what does|what are|how does|explain|describe|summarise|summarize)\b/i,
    /\b(git status|git log|git diff|git branch)\b/i,
    /\b(yfinance|finviz|screener|yahoo|rss|data pull|fetch data)\b/i,
    /\b(run tests|execute tests|check tests|test results)\b/i,
    /\b(status|current state|where are we|what.s active)\b/i,
  ]},

  // Tier 3 — architecture/multi-file/bias/phase planning (check BEFORE tier 2)
  { tier: 3, patterns: [
    /\b(architect|redesign|rethink|overhaul|restructure|rewrite)\b/i,
    /\b(bias|bullish bias|bearish bias|directional bias)\b/i,
    /\b(phase [a-e]|new phase|phase planning|roadmap)\b/i,
    /\b(multiple modules|multiple files|across .+ modules|all signals|all dims)\b/i,
    /\b(security|vulnerability|audit|threat model)\b/i,
    /\b(multi.timeframe|regime detection|hmm|hidden markov)\b/i,
    /\b(kelly|position sizing|risk system|risk framework)\b/i,
    /\b(refactor .+ and .+|refactor signals|refactor risk|refactor orders)\b/i,
    /\b(3\+ files|four files|five files|many files)\b/i,
  ]},

  // Tier 2 — standard implementation (catches everything else that's not read-only)
  { tier: 2, patterns: [
    /\b(implement|add|build|create|write|fix|update|change|modify|patch)\b/i,
    /\b(bug|issue|error|broken|failing|crash)\b/i,
    /\b(spec|feature spec|session log|session summary|research doc)\b/i,
    /\b(test|unit test|write test|add test)\b/i,
    /\b(function|method|class|module|endpoint)\b/i,
  ]},
];

// ─── Decifer-specific warnings ────────────────────────────────────────────────
const WARNING_PATTERNS = [
  {
    pattern: /yfinance/i,
    warning: 'yfinance has thread-safety issues — use thread-local instances or wrap in queue',
  },
  {
    pattern: /ta.?lib|talib/i,
    warning: 'TA-Lib requires system C library — check availability before using',
  },
  {
    pattern: /\b(live|production|prod|real money|real account)\b/i,
    warning: 'Paper account only — no live trading. Confirm this is paper mode.',
  },
  {
    pattern: /\bchief.decifer\b|chief_decifer|dashboard/i,
    warning: 'Chief Decifer is read-only — only modify state files in data contract paths, not dashboard code',
  },
  {
    pattern: /\b(main branch|push to main|merge to main|commit main)\b/i,
    warning: 'Never commit directly to main — use a feat/ or fix/ branch',
  },
  {
    pattern: /\b(delete|remove|drop|reset|wipe|clear)\b/i,
    warning: 'Destructive operation detected — confirm with Amit before executing',
  },
  {
    pattern: /\bsignals\.py\b/i,
    warning: 'signals.py is the core signal engine — all 9 dimensions must remain direction-agnostic after any change',
  },
];

// ─── Classify ─────────────────────────────────────────────────────────────────
function classifyTask(description) {
  let assignedTier = 2; // default

  // Match tier patterns in order: 1, 3, 2 (3 before 2 to catch complex tasks)
  const orderedTiers = [1, 3, 2];
  let matched = false;

  for (const t of orderedTiers) {
    const entry = TIER_PATTERNS.find(p => p.tier === t);
    if (!entry) continue;
    for (const pattern of entry.patterns) {
      if (pattern.test(description)) {
        assignedTier = t;
        matched = true;
        break;
      }
    }
    if (matched) break;
  }

  // Collect warnings
  const warnings = WARNING_PATTERNS
    .filter(w => w.pattern.test(description))
    .map(w => w.warning);

  const tier = TIERS[assignedTier];

  return {
    tier: assignedTier,
    label: tier.label,
    description: tier.description,
    approach: tier.approach,
    requiresApproval: tier.requiresApproval || false,
    maxRoundTrips: tier.maxRoundTrips,
    warnings,
  };
}

// ─── Format output ────────────────────────────────────────────────────────────
function formatText(task, result) {
  const lines = [
    `Task: "${task}"`,
    `Tier ${result.tier} — ${result.label}: ${result.description}`,
    `Approach: ${result.approach}`,
  ];
  if (result.requiresApproval) {
    lines.push('⚠️  REQUIRES AMIT APPROVAL before writing any code.');
  }
  if (result.warnings.length) {
    lines.push('\nWarnings:');
    result.warnings.forEach(w => lines.push(`  ⚠  ${w}`));
  }
  return lines.join('\n');
}

// ─── Entry point ──────────────────────────────────────────────────────────────
let taskDescription = process.argv.slice(2).join(' ');

if (!taskDescription) {
  try {
    taskDescription = readFileSync('/dev/stdin', 'utf-8').trim();
  } catch {
    taskDescription = '';
  }
}

if (!taskDescription) {
  console.error('Usage: echo "task description" | node task-router.mjs');
  console.error('   or: node task-router.mjs "task description"');
  process.exit(1);
}

import { readFileSync } from 'fs';

const result = classifyTask(taskDescription);
const isJson = process.argv.includes('--json');

if (isJson) {
  console.log(JSON.stringify({ task: taskDescription, ...result }, null, 2));
} else {
  console.log(formatText(taskDescription, result));
}
