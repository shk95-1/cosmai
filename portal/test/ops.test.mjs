import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  isProblem, problems, problemCount, severityOf, sortByWorst, byArm, disabled,
  relativeTime, describeInterval, ARM_ORDER,
} from '../public/ops.js';
import { OPS_QUERY } from '../public/query.js';

const row = (over) => ({
  stage_key: 'commerce:ranking', arm: 'commerce', dataset: 'ranking', enabled: true,
  expected_interval: '01:00:00', last_success_at: null, last_run_at: null,
  last_run_status: null, overdue_by: null, freshness: 'ok',
  requests: null, ok: null, blocked: null, failed: null, p90_ms: null, ...over,
});

const NOW = new Date('2026-08-26T12:00:00Z');

test('never 와 disabled 는 문제로 세지 않는다 — 항상 빨간 배너는 아무도 안 본다', () => {
  assert.equal(isProblem(row({ freshness: 'never' })), false);
  assert.equal(isProblem(row({ freshness: 'disabled' })), false);
  assert.equal(isProblem(row({ freshness: 'late' })), true);
  assert.equal(isProblem(row({ freshness: 'stalled' })), true);
});

test('freshness 가 ok 여도 마지막 run 이 통째로 실패했으면 문제다', () => {
  assert.equal(isProblem(row({ freshness: 'ok', last_run_status: 'failed' })), true);
  assert.equal(isProblem(row({ freshness: 'ok', last_run_status: 'ok' })), false);
});

test('partial 은 배너를 빨갛게 만들지 않는다 — 돌았고 대부분을 걷었다 (#156)', () => {
  // commerce:product is partial 3 days out of 4 (collecting 84-86 of 89). Counting that as a stuck stage
  // would turn the banner red every day, and the trap #154 removed from the view would just move up one level to the screen.
  const partial = row({ freshness: 'ok', last_run_status: 'partial' });
  assert.equal(isProblem(partial), false);
  assert.equal(problemCount([partial]), 0);
  // It does not disappear, though: it still stands out in the full list with the warning color, and the failed count stays in the stats.
  assert.equal(severityOf(partial), 1);
});

test('배너의 수와 문제 덩어리의 행 수는 같은 술어에서 나온다', () => {
  const rows = [
    row({ stage_key: 'a', freshness: 'ok' }),
    row({ stage_key: 'b', freshness: 'late' }),
    row({ stage_key: 'c', freshness: 'never' }),
    row({ stage_key: 'd', freshness: 'ok', last_run_status: 'failed' }),
  ];
  assert.equal(problemCount(rows), 2);
  assert.equal(problems(rows).length, problemCount(rows));
});

test('나쁜 것이 위로 서고, 동률은 stage_key 로 깨져 순서가 흔들리지 않는다', () => {
  const rows = [
    row({ stage_key: 'z', freshness: 'ok' }),
    row({ stage_key: 'm', freshness: 'stalled' }),
    row({ stage_key: 'a', freshness: 'late' }),
    row({ stage_key: 'b', freshness: 'late' }),
  ];
  assert.deepEqual(sortByWorst(rows).map((r) => r.stage_key), ['m', 'a', 'b', 'z']);
  // The result must be the same even when the same input is fed in a different order.
  assert.deepEqual(sortByWorst([...rows].reverse()).map((r) => r.stage_key), ['m', 'a', 'b', 'z']);
});

test('색을 고르는 심각도는 두 값 중 나쁜 쪽이고, 둘은 같은 자로 잰다', () => {
  // "Stalled for 3 days" and "just failed" are both critical — which one is redder is not a question to ask.
  // The screen tells them apart not by color but by showing both values side by side (#138).
  assert.equal(severityOf(row({ freshness: 'stalled', last_run_status: 'ok' })), 0);
  assert.equal(severityOf(row({ freshness: 'ok', last_run_status: 'failed' })), 0);
  assert.equal(severityOf(row({ freshness: 'late', last_run_status: 'ok' })), 1);
  assert.equal(severityOf(row({ freshness: 'ok', last_run_status: 'ok' })), 3);
  assert.equal(severityOf(row({ freshness: 'disabled', last_run_status: null })), 4);
  // A run that stepped aside for a source lock is not a failure (#78) — it stands at the same level as ok.
  assert.equal(severityOf(row({ freshness: 'ok', last_run_status: 'yielded' })), 3);
});

test('팔의 순서는 고정이고 빈 팔은 서지 않는다', () => {
  const rows = [
    row({ stage_key: 'analyze:all', arm: 'analyze' }),
    row({ stage_key: 'commerce:ranking', arm: 'commerce' }),
  ];
  assert.deepEqual(byArm(rows).map((g) => g.arm), ['commerce', 'analyze']);
  assert.deepEqual(ARM_ORDER, ['commerce', 'youtube', 'naver', 'analyze']);
});

test('disabled 는 팔 목록에서 빠지고 제 자리로 간다', () => {
  const rows = [
    row({ stage_key: 'youtube:watch', arm: 'youtube', freshness: 'disabled' }),
    row({ stage_key: 'youtube:work', arm: 'youtube', freshness: 'ok' }),
  ];
  assert.deepEqual(byArm(rows).flatMap((g) => g.rows).map((r) => r.stage_key), ['youtube:work']);
  assert.deepEqual(disabled(rows).map((r) => r.stage_key), ['youtube:watch']);
});

test('상대시각은 없을 때 대시를 내고 미래로 그리지 않는다', () => {
  assert.equal(relativeTime(null, NOW), '—');
  assert.equal(relativeTime('2026-08-26T11:58:00Z', NOW), '2분 전');
  assert.equal(relativeTime('2026-08-26T09:00:00Z', NOW), '3시간 전');
  assert.equal(relativeTime('2026-08-24T12:00:00Z', NOW), '2일 전');
  // Even a clock skew producing a future time never draws something like '-3 minutes ago.'
  assert.equal(relativeTime('2026-08-26T12:03:00Z', NOW), '방금');
});

test('주기는 사람이 읽는 말로 바뀌고 모르는 모양은 그대로 남는다', () => {
  assert.equal(describeInterval('01:00:00'), '매시');
  assert.equal(describeInterval('00:05:00'), '5분마다');
  assert.equal(describeInterval('1 day'), '매일');
  assert.equal(describeInterval('7 days'), '7일마다');
  assert.equal(describeInterval('1 mon'), '1 mon');
  assert.equal(describeInterval(null), '—');
});

test('select 가 소비 함수들이 거르는 컬럼을 빠짐없이 담는다 (#130 이 데인 자리)', () => {
  // Writing down here the keys a consuming function reads catches a change where the function starts reading a
  // new column but select was never updated. Without it in select, the response row has no such key and the comparison is always false.
  for (const key of ['freshness', 'last_run_status', 'arm', 'stage_key', 'last_success_at']) {
    assert.ok(OPS_QUERY.select.includes(key), `select 에 ${key} 가 없다`);
  }
});
