import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { latestRuns, scopesForRun, needRowsForScope, wishRowsForScope, productRows, runCaption } from '../public/screens.js';

const here = dirname(fileURLToPath(import.meta.url));
const needFixture = JSON.parse(readFileSync(join(here, 'fixtures/metrics_need.sample.json'), 'utf8'));
const wishFixture = JSON.parse(readFileSync(join(here, 'fixtures/metrics_wish.sample.json'), 'utf8'));
const runsFixture = JSON.parse(readFileSync(join(here, 'fixtures/analysis_run.sample.json'), 'utf8'));

// 시드는 슬라이스별로 다른 run(need=2, wish=3, 에픽 #16 §1단계 판정 4) — 표마다
// 자기 run을 써야 한다. 하나의 runId를 공유하면 wish가 항상 빈다(수정 라운드 1 결함 1).
test('latestRuns: need와 wish는 각자의 최신 run_id를 갖는다', () => {
  const { needRunId, wishRunId } = latestRuns(runsFixture, needFixture, wishFixture);
  assert.equal(needRunId, 2);
  assert.equal(wishRunId, 3);
});

// #87: 손으로 돌린 aggregate 는 note 로 기존 run 을 재사용해 더 작은 run_id 에 쓴다
// (analysis/aggregate/pipeline.py의 _run_id) — max(run_id) 로는 그 run 이 안 보인다.
// analysis_run.finished_at·status='ok' 로 "가장 나중에 끝난 run"을 골라야 한다.
test('latestRuns: run_id 크기가 아니라 finished_at·status로 최신을 고른다 (#87)', () => {
  const runs = [
    { run_id: 2, status: 'ok', finished_at: '2026-02-01T00:00:00Z', versions: { aggregate: '1.0.0' }, note: 'analyze all' },
    { run_id: 1, status: 'ok', finished_at: '2026-03-01T00:00:00Z', versions: { aggregate: '1.1.0' }, note: 'aggregate:1.1.0:all' },
  ];
  const need = [
    { run_id: 1, scope: 'a', need_key: 'x' },
    { run_id: 2, scope: 'a', need_key: 'y' },
  ];
  const { needRunId, needRun } = latestRuns(runs, need, []);
  assert.equal(needRunId, 1);
  assert.equal(needRun.note, 'aggregate:1.1.0:all');
});

// status='running'(끝나지 않은 run)은 화면에 아직 보이면 안 된다 — finished_at 도 아직 없다.
test('latestRuns: status가 ok가 아닌 run은 건너뛴다', () => {
  const runs = [
    { run_id: 1, status: 'ok', finished_at: '2026-01-01T00:00:00Z', versions: {}, note: 'old' },
    { run_id: 2, status: 'running', finished_at: null, versions: {}, note: 'new but unfinished' },
  ];
  const need = [{ run_id: 1, scope: 'a' }, { run_id: 2, scope: 'a' }];
  const { needRunId } = latestRuns(runs, need, []);
  assert.equal(needRunId, 1);
});

test('wishRowsForScope: wish 자신의 run(3)으로 걸러야 행이 나온다', () => {
  const { wishRunId } = latestRuns(runsFixture, needFixture, wishFixture);
  const rows = wishRowsForScope(wishFixture, wishRunId, 'wish:a');
  assert.equal(rows.length, 4);
});

// 결함 2 재현: wish scope 셀렉트를 need scope(카테고리명)로 채우면 wish:* 값이
// 하나도 없다 — 화면마다 자기 표의 scope 목록을 써야 한다.
test('scopesForRun: need scope와 wish scope는 서로 다른 값 집합이다', () => {
  const { needRunId, wishRunId } = latestRuns(runsFixture, needFixture, wishFixture);
  const needScopes = scopesForRun(needFixture, needRunId);
  const wishScopes = scopesForRun(wishFixture, wishRunId);
  assert.deepEqual(needScopes, ['선블록', '쿠션']);
  assert.deepEqual(wishScopes, ['wish:a', 'wish:b']);
  assert.equal(needScopes.some((s) => wishScopes.includes(s)), false);
});

test('needRowsForScope: 카테고리 합(product_ref/month 빈 값)만 남는다', () => {
  const { needRunId } = latestRuns(runsFixture, needFixture, wishFixture);
  const rows = needRowsForScope(needFixture, needRunId, '선블록');
  assert.deepEqual(rows.map((r) => r.need_key), ['밀림', '끈적유분']);
});

test('productRows: run 2의 product_ref 비어있지 않은 행이 나온다', () => {
  const { needRunId } = latestRuns(runsFixture, needFixture, wishFixture);
  const rows = productRows(needFixture, needRunId);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].product_ref, 'oy:A1');
});

// #87: 캡션이 각 run의 versions·note 를 보여야 손 재집계가 실제로 반영됐는지 알 수 있다.
test('runCaption: need·wish run의 versions·note를 보인다 (#87)', () => {
  const needRun = { run_id: 2, note: 'aggregate:1.1.0:all', versions: { aggregate: '1.1.0' } };
  const wishRun = { run_id: 3, note: 'aggregate:1.1.0:wish', versions: { aggregate: '1.1.0' } };
  const caption = runCaption(needRun, wishRun);
  assert.match(caption, /#2/);
  assert.match(caption, /#3/);
  assert.match(caption, /aggregate:1\.1\.0:all/);
  assert.match(caption, /aggregate:1\.1\.0:wish/);
  assert.match(caption, /"aggregate":"1\.1\.0"/);
  assert.equal(runCaption(null, null), '데이터 없음');
});
