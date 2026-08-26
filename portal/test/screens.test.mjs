import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import {
  latestRuns, scopesForRun, needRowsForScope, wishRowsForScope, productRows, runCaption,
  safeRatio, needCharacterRows, hasYoutubeMentions, rowsWithValue,
} from '../public/screens.js';

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

// #41: 제품 축 행은 scope 마다 한 벌씩 나온다 — 같은 제품이 자기 카테고리와 롤업('all')에서
// 두 번 걸리면 상위 20이 중복으로 찬다. 롤업이 있으면 롤업만 본다.
test('productRows: 롤업 scope 가 있으면 제품이 두 번 나오지 않는다 (#41)', () => {
  const need = [
    { run_id: 5, scope: '선블록', need_key: '밀림', month: '', product_ref: '', neg: 9, unresolved: 0.6 },
    { run_id: 5, scope: '선블록', need_key: '밀림', month: '', product_ref: 'oy:A1', neg: 4, unresolved: 0.8 },
    { run_id: 5, scope: 'all', need_key: '밀림', month: '', product_ref: 'oy:A1', neg: 4, unresolved: 0.8 },
    { run_id: 5, scope: 'all', need_key: '밀림', month: '', product_ref: 'oy:B2', neg: 2, unresolved: 0.5 },
  ];
  const rows = productRows(need, 5);
  assert.deepEqual(rows.map((r) => [r.scope, r.product_ref]), [['all', 'oy:A1'], ['all', 'oy:B2']]);
});

// --scope 로 좁혀 돈 run 에는 'all' 이 없다 — 그때는 있는 scope 를 그대로 쓴다.
test('productRows: 롤업이 없는 run 은 카테고리 scope 의 제품 행을 낸다 (#41)', () => {
  const need = [
    { run_id: 6, scope: '선블록', need_key: '밀림', month: '', product_ref: 'oy:A1', neg: 4, unresolved: 0.8 },
  ];
  assert.deepEqual(productRows(need, 6).map((r) => r.product_ref), ['oy:A1']);
});

// ---- 화면 4: 니즈의 성격 --------------------------------------------------

test('safeRatio: 분모가 0·없음이면 null 이다 (0 이 아니다)', () => {
  assert.equal(safeRatio(3, 4), 0.75);
  assert.equal(safeRatio(0, 4), 0);
  assert.equal(safeRatio(3, 0), null);
  assert.equal(safeRatio(3, null), null);
  assert.equal(safeRatio(null, 4), null);
  assert.equal(safeRatio(undefined, undefined), null);
});

test('needCharacterRows: 카테고리 합 행에 세 비율을 얹는다', () => {
  const rows = needCharacterRows(needFixture, 2, '선블록');
  assert.deepEqual(rows.map((r) => r.need_key), ['밀림', '끈적유분']);
  const 밀림 = rows[0];
  assert.equal(밀림.persist_month_ratio, 5 / 6);
  assert.equal(밀림.persist_product_ratio, 8 / 10);
  assert.equal(밀림.new_ratio, 0.3 / 0.71);
  assert.equal(밀림.low_share, 0.44);
});

// unresolved 가 0 인 니즈(불만이 다 해소됨)에서 신규 비율은 계산될 수 없다.
test('needCharacterRows: unresolved 가 0 이면 신규 비율은 null 이다', () => {
  const need = [{ run_id: 9, scope: 'a', need_key: 'x', month: '', product_ref: '', unresolved: 0, unresolved_new: 0, persist_months: 1, persist_months_total: 2, persist_products: 1, persist_products_total: 2 }];
  const rows = needCharacterRows(need, 9, 'a');
  assert.equal(rows[0].new_ratio, null);
  assert.equal(rows[0].persist_month_ratio, 0.5);
});

test('needCharacterRows: 제품 축 행은 섞이지 않는다 (#41)', () => {
  const rows = needCharacterRows(needFixture, 2, '선블록');
  assert.equal(rows.some((r) => r.product_ref !== ''), false);
});

test('hasYoutubeMentions: yt_neg·yt_pos 가 전부 0 인 scope 는 false', () => {
  assert.equal(hasYoutubeMentions(needCharacterRows(needFixture, 2, '선블록')), true);
  assert.equal(hasYoutubeMentions(needCharacterRows(needFixture, 2, '쿠션')), false);
  assert.equal(hasYoutubeMentions([]), false);
});

test('rowsWithValue: 비율이 null 인 행은 막대에서 빠진다', () => {
  const rows = [{ need_key: 'a', new_ratio: 0.5 }, { need_key: 'b', new_ratio: null }, { need_key: 'c', new_ratio: 0 }];
  assert.deepEqual(rowsWithValue(rows, 'new_ratio').map((r) => r.need_key), ['a', 'c']);
});
