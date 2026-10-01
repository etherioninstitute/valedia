'use strict';
// self-continuation.js — Flux P0-3 drop-in for Valedia (paper/ops).
// classifyDecision / decide / onStop — four USER gates only; else continue.
// Tuned patterns: --live/orders, send/email, wallet, LMS unload, exclusive Build kill.

const fs = require('fs');
const path = require('path');

const GATES = [
  {
    gate: 'irreversible',
    hint: 'destroys/overwrites data, LMS unload, exclusive Build kill, force-push, not cheaply undoable',
    test: (q) =>
      /\b(delete|destroy|drop\s+table|force[-\s]?push|rm\s+-rf|overwrite|wipe|truncate|purge|permanent(ly)?)\b/i.test(q) ||
      /\b(unload|swap)\b.*\b(lms|model|ornith|qwen|bionic)\b/i.test(q) ||
      /\b(lms|model|ornith|qwen|bionic)\b.*\b(unload|swap)\b/i.test(q) ||
      /\b(kill|quit|wipe)\b.*\b(flux-grok|valedia-grok|exclusive\s+build|screen\s+session)\b/i.test(q) ||
      /\b(flux-grok|valedia-grok)\b.*\b(kill|quit|wipe)\b/i.test(q) ||
      /\bdocker\s+compose\s+down\s+-v\b/i.test(q) ||
      /\bvolume\s+wipe\b/i.test(q),
  },
  {
    gate: 'outward-facing',
    hint: 'sends to real people / publishes / spends / deploys / --live / orders / wallet',
    test: (q) =>
      /\b(send|e-?mail|publish|deploy|post\b|tweet|dm\b|charge|refund|pay(ment)?|invoice|upload.*(prod|s3|live)|go[-\s]?live)\b/i.test(q) ||
      /(?:^|[\s"'`])--live\b/i.test(q) ||
      /\b(create_order|place\s+order|amend\s+order|cancel\s+order|venue\s+order)\b/i.test(q) ||
      /\b(wallet|transfer|deposit|sweep|spend)\b/i.test(q) ||
      /\b(WEATHER_LIVE|OKX_LIVE|POLYMARKET_LIVE)\b/i.test(q),
  },
  {
    gate: 'preference',
    hint: 'pure taste — no derivable best answer',
    test: (q) =>
      /\b(prefer|your\s+call|taste|opinion|brand\s+voice|do\s+you\s+like)\b/i.test(q) ||
      /\bwhich\s+(?:\w+\s+){0,2}(name|colou?r|font|style|tone|voice|theme|design)\b/i.test(q),
  },
  {
    gate: 'resource-blocked',
    hint: 'needs a credential / access / Burton pin the agent lacks',
    test: (q) =>
      /\b(credentials?|api[-\s]?keys?|secrets?|passwords?|access\s+tokens?|permissions?|login|auth|2fa|otp)\b/i.test(q) ||
      /\b(need|needs|missing)\b.*\b(burton\s+yes|same-turn\s+yes|pin|unlock)\b/i.test(q),
  },
];

function classifyDecision(question) {
  const q = String(question || '');
  for (const g of GATES) {
    if (g.test(q)) return { gate: 'user', category: g.gate, hint: g.hint };
  }
  return { gate: 'self', category: 'self-answerable', hint: 'reversible + internal + derivable' };
}

function decide(question, options = [], choose) {
  const c = classifyDecision(question);
  if (c.gate === 'user') {
    return { continue: false, category: c.category, hint: c.hint, question, options };
  }
  const chosen = typeof choose === 'function' ? choose(options) : options[0];
  return {
    continue: true,
    chosen,
    category: c.category,
    directive: 'Chose best option; safe/reversible/internal; continue.',
    whySafe: c.hint,
  };
}

function onStop({ pendingWork } = {}) {
  if (!pendingWork) return { allow: true };
  return {
    allow: false,
    directive:
      'Work remains — execute the next item, do not stop. ' +
      'STOP + ASK only if the pending decision is irreversible, outward-facing, pure preference, ' +
      'or resource-blocked. Otherwise decide (best option + one-line why-safe) and CONTINUE. ' +
      'Valedia hard gates: --live/orders, send/email, wallet, LMS unload, kill flux-grok/valedia-grok.',
  };
}

function maybeLog(record) {
  if (process.env.SELF_CONTINUATION_LOG !== '1') return;
  try {
    const dir =
      process.env.SELF_CONTINUATION_LOG_DIR ||
      path.join(process.env.HOME || '', '.openclaw/workspace-v2/data');
    fs.mkdirSync(dir, { recursive: true });
    const line = JSON.stringify({ ts: new Date().toISOString(), ...record }) + '\n';
    fs.appendFileSync(path.join(dir, 'self-continuation-log.jsonl'), line);
  } catch (_) {
    /* never throw */
  }
}

function cli(argv) {
  const cmd = argv[0] || 'help';
  if (cmd === 'classify') {
    const q = argv.slice(1).join(' ') || '';
    const out = classifyDecision(q);
    maybeLog({ op: 'classify', question: q, ...out });
    process.stdout.write(JSON.stringify(out, null, 2) + '\n');
    return out.gate === 'user' ? 2 : 0;
  }
  if (cmd === 'decide') {
    const q = argv[1] || '';
    const options = argv.slice(2);
    const out = decide(q, options);
    maybeLog({ op: 'decide', question: q, options, ...out });
    process.stdout.write(JSON.stringify(out, null, 2) + '\n');
    return out.continue ? 0 : 2;
  }
  if (cmd === 'on-stop') {
    const pending = /^(1|true|yes|pending)$/i.test(String(argv[1] || ''));
    const out = onStop({ pendingWork: pending });
    maybeLog({ op: 'on-stop', pendingWork: pending, ...out });
    process.stdout.write(JSON.stringify(out, null, 2) + '\n');
    return out.allow ? 0 : 3;
  }
  process.stdout.write(
    'usage:\n' +
      '  self-continuation.js classify <question>\n' +
      '  self-continuation.js decide <question> [option...]\n' +
      '  self-continuation.js on-stop <true|false>\n' +
      'exit: 0 continue/allow, 2 user-gate, 3 stop-blocked\n'
  );
  return 1;
}

module.exports = { classifyDecision, decide, onStop, GATES, maybeLog };

if (require.main === module) {
  process.exitCode = cli(process.argv.slice(2));
}
