#!/usr/bin/env node
// Issue #6: run the real share.js loadData() with deterministic API fixtures.
// Usage: node scripts/repro-share-done-todos.cjs
// No server, browser, database, or external dependencies are required.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const sourcePath = path.join(__dirname, '../app/ui/share.js');
const source = fs.readFileSync(sourcePath, 'utf8')
  .replace(/^import .* from "\.\/i18n\.js";\s*$/m, '')
  .replace(/^export function openShare\(/m, 'function openShare(');
const sandbox = vm.createContext({
  localStorage: { getItem: () => null },
  structuredClone,
});
// Only module syntax is adapted. The production range, filter, and aggregation
// execute unchanged; the appended hook exposes their result to this script.
vm.runInContext(`${source}
  globalThis.reproduce = async (period, today, todos) => {
    opts.period = period;
    ctx = {
      todayStr: () => today,
      shiftDay: (day, n) => {
        const d = new Date(day + 'T12:00:00+09:00');
        d.setUTCDate(d.getUTCDate() + n);
        return new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Tokyo' }).format(d);
      },
      api: async (url) => {
        if (url === '/v1/todos?status=done&limit=200') return { todos };
        if (url.startsWith('/v1/stats/daily?')) return { days: [] };
        if (url.startsWith('/v1/sessions?')) return { sessions: [] };
        if (url.startsWith('/v1/timeline?')) return { items: [] };
        throw new Error('Unexpected API request: ' + url);
      },
    };
    await loadData();
    return data;
  };
`, sandbox, { filename: sourcePath });

let passed = 0;
let failed = 0;
function check(label, actual, expected) {
  try {
    assert.equal(actual, expected);
    passed++;
  } catch (err) {
    if (!(err instanceof assert.AssertionError)) throw err;
    failed++;
    console.error(`FAIL ${label}: expected=${expected} actual=${actual}`);
  }
}

function jst(milliseconds) {
  return new Date(milliseconds + 9 * 60 * 60 * 1000)
    .toISOString().replace('Z', '+09:00');
}

async function main() {
  console.log(`Runtime: ${process.version}; host TZ=${Intl.DateTimeFormat().resolvedOptions().timeZone}`);
  const from = '2026-10-14';
  const fromMs = Date.parse(`${from}T00:00:00+09:00`);
  const periods = [
    { period: 'day', today: '2026-10-14', end: '2026-10-15' },
    { period: 'week', today: '2026-10-20', end: '2026-10-21' },
    { period: 'month', today: '2026-11-12', end: '2026-11-13' },
  ];
  for (const { period, today, end } of periods) {
    const endMs = Date.parse(`${end}T00:00:00+09:00`);
    const cases = [
      ['first-day 00:00', fromMs, true],
      ['first-day 00:30', fromMs + 30 * 60 * 1000, true],
      ['first-day 08:59', fromMs + (8 * 60 + 59) * 60 * 1000, true],
      ['first-day 09:00', fromMs + 9 * 60 * 60 * 1000, true],
      ['fromAt - 1ms', fromMs - 1, false],
      ['toAt - 1ms', endMs - 1, true],
      ['toAt exactly', endMs, false],
      ['toAt + 1ms', endMs + 1, false],
    ];
    const empty = await sandbox.reproduce(period, today, []);
    check(`${period} range.from`, empty.from, from);
    check(`${period} range.to`, empty.to, today);
    for (const [label, milliseconds, expected] of cases) {
      const results = [];
      for (const [format, done_at] of [['Z', new Date(milliseconds).toISOString().replace('.000Z', 'Z')], ['+09:00', jst(milliseconds).replace('.000+', '+')]]) {
        const result = await sandbox.reproduce(period, today, [{ id: 'fixture', done_at }]);
        const included = result.doneTodos.some((todo) => todo.id === 'fixture');
        results.push(included);
        check(`${period} ${label} ${format} (${done_at})`, included, expected);
      }
      check(`${period} ${label} equivalent Z/+09:00`, results[0], results[1]);
    }
    for (const done_at of ['not-a-date', '', null, undefined]) {
      const result = await sandbox.reproduce(period, today, [{ id: 'invalid', done_at }]);
      check(`${period} invalid/empty done_at=${String(done_at)}`, result.doneTodos.length, 0);
    }
  }
  console.log(`Results: PASS=${passed} FAIL=${failed} TOTAL=${passed + failed}`);
  process.exitCode = failed ? 1 : 0;
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 2;
});
