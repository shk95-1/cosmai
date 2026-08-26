import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import {
  latestRuns, scopesForRun, needRowsForScope, wishRowsForScope, productRows, runCaptionParts,
  safeRatio, needCharacterRows, hasYoutubeMentions, rowsWithValue, defaultScope,
  productNameIndex, productLabel, truncateLabel, withProductNames,
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
// #122: 그 전부를 헤더에 펴면 네 줄을 먹는다 — 한 줄 요약과 접히는 상세로 나눈다.
test('runCaptionParts: 요약은 한 줄, versions·note 는 상세로 간다 (#87, #122)', () => {
  const needRun = { run_id: 2, finished_at: '2026-08-26T05:01:31.074893+00:00', note: 'aggregate:1.1.0:all', versions: { aggregate: '1.1.0', extractor: 'rule-v2.3' } };
  const wishRun = { run_id: 3, finished_at: '2026-08-26T06:02:00+00:00', note: 'aggregate:1.1.0:wish', versions: { aggregate: '1.1.0' } };
  const { summary, detail } = runCaptionParts(needRun, wishRun);
  assert.match(summary, /#2 · 08-26 05:01 · extractor rule-v2\.3/);
  assert.match(summary, /#3 · 08-26 06:02 · aggregate 1\.1\.0/);
  assert.equal(summary.includes('\n'), false);
  assert.doesNotMatch(summary, /aggregate:1\.1\.0:all/); // note 는 요약에 없다
  assert.match(detail, /aggregate:1\.1\.0:all/);
  assert.match(detail, /aggregate:1\.1\.0:wish/);
  assert.match(detail, /"aggregate":"1\.1\.0"/);
  assert.equal(runCaptionParts(null, null).summary, '데이터 없음');
});

// 실제로는 한 analyze run 이 두 표를 다 쓴다(run #24) — 같은 것을 두 번 적으면
// 요약이 그만큼 길어져 애초에 접으려던 이유가 사라진다.
test('runCaptionParts: need 와 wish 가 같은 run 이면 한 번만 적는다', () => {
  const run = { run_id: 24, finished_at: '2026-08-26T05:01:31+00:00', note: 'analyze:all', versions: { extractor: 'rule-v2.3' } };
  const { summary } = runCaptionParts(run, run);
  assert.equal((summary.match(/#24/g) || []).length, 1);
  assert.match(summary, /need/);
  assert.match(summary, /wish/);
});

// finished_at 을 Date 로 파싱해 지역시간으로 찍으면 캡션이 보는 기계마다 달라진다.
test('runCaptionParts: 시각은 ISO 문자열 그대로(UTC) 자른다', () => {
  const run = { run_id: 7, finished_at: '2026-12-31T23:59:59+00:00', versions: {} };
  assert.match(runCaptionParts(run, null).summary, /12-31 23:59/);
  assert.match(runCaptionParts({ run_id: 8 }, null).summary, /#8/); // finished_at 없어도 죽지 않는다
});

// #122: 셀렉트의 첫 항목이 알파벳 순 첫 scope 라 "01 > 마스크팩 > 시트팩" 이 첫 화면이었다.
test('defaultScope: 롤업 all 이 있으면 all 이다', () => {
  const rows = [
    { run_id: 1, scope: '01 > 마스크팩 > 시트팩' },
    { run_id: 1, scope: '01 > 마스크팩 > 시트팩' },
    { run_id: 1, scope: 'all' },
    { run_id: 2, scope: '다른 run' },
  ];
  assert.equal(defaultScope(rows, 1), 'all');
});

test('defaultScope: all 이 없으면 행이 가장 많은 scope 다', () => {
  const rows = [
    { run_id: 1, scope: 'wish:a' },
    { run_id: 1, scope: 'wish:b' },
    { run_id: 1, scope: 'wish:b' },
  ];
  assert.equal(defaultScope(rows, 1), 'wish:b');
  assert.equal(defaultScope([], 1), null);
  assert.equal(defaultScope(rows, 9), null);
});

// 동률이면 사전순 — 새로고침마다 첫 화면이 바뀌면 무엇을 보고 있는지 알 수 없다.
test('defaultScope: 동률은 사전순으로 끊는다', () => {
  const rows = [{ run_id: 1, scope: 'b' }, { run_id: 1, scope: 'a' }];
  assert.equal(defaultScope(rows, 1), 'a');
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


// ---- 화면 3: 제품 이름 (#122 §10) -----------------------------------------

// 화면 3 은 metrics_need 의 ref 만 갖고 있어 'oy:A000000149577' 이 막대 라벨이 된다.
// needs.product_ref 에 brand·name 이 있고 anon 화이트리스트에도 들어 있다(#11 입력).
const CATALOG = [
  { product_ref: 'oy:A000000149577', brand: '메디힐', name: '메디힐 티트리 임팩트인 밸런싱 마스크 10매', name_norm: '티트리 임팩트인 밸런싱 마스크' },
  { product_ref: 'da:1079392', brand: '본셉 메이크업', name: '[05 바닐라워터] 본셉 워터 베일 틴트', name_norm: '본셉 워터 베일 틴트' },
  { product_ref: 'da:9', brand: '', name: '이름만 있는 제품', name_norm: '' },
];

// name 은 '[8월올영픽/트러블손절크림] … 80ml 1+1 기획' 처럼 기획 문구와 용량을 달고 있다 —
// name_norm 이 그것을 걷어낸 이름이라 라벨에는 그쪽이 맞다.
test('productLabel: 카탈로그에 있으면 브랜드 · 제품명이다', () => {
  const index = productNameIndex(CATALOG);
  assert.equal(productLabel('oy:A000000149577', index), '메디힐 · 티트리 임팩트인 밸런싱 마스크');
  assert.equal(productLabel('da:1079392', index), '본셉 메이크업 · 본셉 워터 베일 틴트');
  assert.equal(productLabel('da:9', index), '이름만 있는 제품'); // 브랜드가 없으면 이름만
});

// 링커가 못 붙인 mention 은 사이트의 원래 키가 그대로 ref 가 된다(aggregate 의
// _product: product_ref or source_product_key). 그 자리에 이름을 지어 주면 화면이
// 파이프라인이 하지 않은 연결을 주장한다 — ref 를 그대로 보인다.
test('productLabel: 카탈로그에 없는 ref 는 ref 그대로다', () => {
  const index = productNameIndex(CATALOG);
  assert.equal(productLabel('A000000186166', index), 'A000000186166');
  assert.equal(productLabel('101473', new Map()), '101473');
});

test('productNameIndex: product_ref 없는 행은 담지 않는다', () => {
  const index = productNameIndex([...CATALOG, { product_ref: '', brand: 'x', name: 'y' }, null]);
  assert.equal(index.size, 3);
  assert.equal(productNameIndex(null).size, 0);
});

// 막대의 라벨 자리는 폭이 정해져 있어 긴 이름은 옆 막대 위로 넘친다 — 자르고 전체
// 이름은 <title>(호버) 몫이다.
test('truncateLabel: 자리를 넘는 이름만 말줄임한다', () => {
  assert.equal(truncateLabel('짧은이름', 10), '짧은이름');
  assert.equal(truncateLabel('메디힐 · 티트리 임팩트인 밸런싱 마스크', 10), '메디힐 · 티트리…');
});

test('withProductNames: 행마다 전체 라벨과 짧은 라벨을 얹는다', () => {
  const index = productNameIndex(CATALOG);
  const rows = withProductNames([
    { product_ref: 'oy:A000000149577', unresolved: 1 },
    { product_ref: 'A000000186166', unresolved: 0.5 },
  ], index);
  assert.equal(rows[0].product, '메디힐 · 티트리 임팩트인 밸런싱 마스크');
  assert.equal(rows[1].product, 'A000000186166');
  assert.ok(rows[0].product_short.length < rows[0].product.length);
  assert.equal(rows[0].unresolved, 1); // 원래 컬럼은 그대로 남는다
  assert.equal(rows[1].product_ref, 'A000000186166');
});
