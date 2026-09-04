import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { NEED_QUERIES } from '../public/query.js';
import {
  latestRuns, scopesForRun, needRowsForScope, wishRowsForScope, productRows, runCaptionParts,
  safeRatio, needCharacterRows, hasYoutubeMentions, rowsWithValue, defaultScope,
  productNameIndex, productLabel, truncateLabel, withProductNames,
  monthRows, monthNeedKeys, hasMonthRows, MONTH_LIMIT,
} from '../public/screens.js';

const here = dirname(fileURLToPath(import.meta.url));
const needFixture = JSON.parse(readFileSync(join(here, 'fixtures/metrics_need.sample.json'), 'utf8'));
const wishFixture = JSON.parse(readFileSync(join(here, 'fixtures/metrics_wish.sample.json'), 'utf8'));
const runsFixture = JSON.parse(readFileSync(join(here, 'fixtures/analysis_run.sample.json'), 'utf8'));

// 시드는 슬라이스별로 다른 run(need=2, wish=3, 에픽 #16 §1단계 판정 4) — 표마다
// must use its own run. Sharing one runId always leaves wish empty (fix round 1 finding 1).
test('latestRuns: need와 wish는 각자의 최신 run_id를 갖는다', () => {
  const { needRunId, wishRunId } = latestRuns(runsFixture, needFixture, wishFixture);
  assert.equal(needRunId, 2);
  assert.equal(wishRunId, 3);
});

// #87: a manually-run aggregate reuses an existing run via note and writes to a smaller run_id
// (analysis/aggregate/pipeline.py's _run_id) — max(run_id) cannot see that run.
// "The run that finished most recently" must be picked by analysis_run.finished_at·status='ok'.
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

// A run with status='running' (not yet finished) must not show on screen yet — it has no finished_at either.
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

// Reproducing finding 2: filling the wish scope select with need scope (category names) leaves not one
// wish:* value present — each screen must use its own table's scope list.
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

// #87: the caption must show each run's versions·note to confirm a manual reaggregation actually took effect.
// #122: laying all of it out in the header eats four lines — split into a one-line summary and collapsible detail.
test('runCaptionParts: 요약은 한 줄, versions·note 는 상세로 간다 (#87, #122)', () => {
  const needRun = { run_id: 2, finished_at: '2026-08-26T05:01:31.074893+00:00', note: 'aggregate:1.1.0:all', versions: { aggregate: '1.1.0', extractor: 'rule-v2.3' } };
  const wishRun = { run_id: 3, finished_at: '2026-08-26T06:02:00+00:00', note: 'aggregate:1.1.0:wish', versions: { aggregate: '1.1.0' } };
  const { summary, detail } = runCaptionParts(needRun, wishRun);
  assert.match(summary, /#2 · 08-26 05:01 · extractor rule-v2\.3/);
  assert.match(summary, /#3 · 08-26 06:02 · aggregate 1\.1\.0/);
  assert.equal(summary.includes('\n'), false);
  assert.doesNotMatch(summary, /aggregate:1\.1\.0:all/); // note is not in the summary
  assert.match(detail, /aggregate:1\.1\.0:all/);
  assert.match(detail, /aggregate:1\.1\.0:wish/);
  assert.match(detail, /"aggregate":"1\.1\.0"/);
  assert.equal(runCaptionParts(null, null).summary, '데이터 없음');
});

// In practice one analyze run writes both tables (run #24) — writing the same thing twice would make
// the summary that much longer, erasing the very reason it was being collapsed.
test('runCaptionParts: need 와 wish 가 같은 run 이면 한 번만 적는다', () => {
  const run = { run_id: 24, finished_at: '2026-08-26T05:01:31+00:00', note: 'analyze:all', versions: { extractor: 'rule-v2.3' } };
  const { summary } = runCaptionParts(run, run);
  assert.equal((summary.match(/#24/g) || []).length, 1);
  assert.match(summary, /need/);
  assert.match(summary, /wish/);
});

// Parsing finished_at with Date and printing local time would make the caption differ on every viewing machine.
test('runCaptionParts: 시각은 ISO 문자열 그대로(UTC) 자른다', () => {
  const run = { run_id: 7, finished_at: '2026-12-31T23:59:59+00:00', versions: {} };
  assert.match(runCaptionParts(run, null).summary, /12-31 23:59/);
  assert.match(runCaptionParts({ run_id: 8 }, null).summary, /#8/); // does not crash even with no finished_at
});

// #122: since a select's first item is the alphabetically first scope, "01 > mask pack > sheet pack" used to be the first screen.
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

// A tie is broken alphabetically — if the first screen changed on every refresh, there would be no telling what was being looked at.
test('defaultScope: 동률은 사전순으로 끊는다', () => {
  const rows = [{ run_id: 1, scope: 'b' }, { run_id: 1, scope: 'a' }];
  assert.equal(defaultScope(rows, 1), 'a');
});

// #41: product-axis rows come out one set per scope — if the same product were caught twice, once in its own
// category and once in the rollup ('all'), the top 20 would fill with duplicates. Only the rollup is read when it exists.
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

// A run narrowed by --scope has no 'all' — in that case the scope that exists is used as-is.
test('productRows: 롤업이 없는 run 은 카테고리 scope 의 제품 행을 낸다 (#41)', () => {
  const need = [
    { run_id: 6, scope: '선블록', need_key: '밀림', month: '', product_ref: 'oy:A1', neg: 4, unresolved: 0.8 },
  ];
  assert.deepEqual(productRows(need, 6).map((r) => r.product_ref), ['oy:A1']);
});

// ---- Screen 4: need character --------------------------------------------------

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

// A new-product ratio cannot be computed for a need whose unresolved is 0 (fully resolved).
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


// ---- Screen 3: product name (#122 §10) -----------------------------------------

// Screen 3 only has metrics_need's ref, so 'oy:A000000149577' becomes the bar label.
// needs.product_ref has brand·name and is also in the anon whitelist (#11's input).
const CATALOG = [
  { product_ref: 'oy:A000000149577', brand: '메디힐', name: '메디힐 티트리 임팩트인 밸런싱 마스크 10매', name_norm: '티트리 임팩트인 밸런싱 마스크' },
  { product_ref: 'da:1079392', brand: '본셉 메이크업', name: '[05 바닐라워터] 본셉 워터 베일 틴트', name_norm: '본셉 워터 베일 틴트' },
  { product_ref: 'da:9', brand: '', name: '이름만 있는 제품', name_norm: '' },
];

// name 은 '[8월올영픽/트러블손절크림] … 80ml 1+1 기획' 처럼 기획 문구와 용량을 달고 있다 —
// name_norm is the name with that stripped, so it is the right one for the label.
test('productLabel: 카탈로그에 있으면 브랜드 · 제품명이다', () => {
  const index = productNameIndex(CATALOG);
  assert.equal(productLabel('oy:A000000149577', index), '메디힐 · 티트리 임팩트인 밸런싱 마스크');
  assert.equal(productLabel('da:1079392', index), '본셉 메이크업 · 본셉 워터 베일 틴트');
  assert.equal(productLabel('da:9', index), '이름만 있는 제품'); // 브랜드가 없으면 이름만
});

// A mention the linker could not attach ends up with the site's original key as its ref as-is (aggregate's
// _product: product_ref or source_product_key). Inventing a name for that spot would have the screen
// assert a link the pipeline never made — the ref is shown as-is instead.
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

// A bar's label slot has a fixed width, so a long name overflows onto the next bar — it is truncated and the
// full name is left to <title> (on hover).
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
  assert.equal(rows[0].unresolved, 1); // the original column is kept as-is
  assert.equal(rows[1].product_ref, 'A000000186166');
});

// ---- Screen 5: period (month) axis (#130) --------------------------------------------

// This PR's regression guard. Even after month rows are mixed into the fixture, screens 1·3·4 must produce
// the same values as before month rows existed — a single leaked month row would read as double the sum row's neg.
// (The test is passing this before month rows are added to the fixture, and still passing after.)
test('월 행이 섞여도 화면 1·3·4 는 같은 값을 낸다 (#130 회귀 방어선)', () => {
  const { needRunId } = latestRuns(runsFixture, needFixture, wishFixture);
  // Screen 1: category-sum rows only — a leaked month row would inflate need_key's count or neg.
  const rows = needRowsForScope(needFixture, needRunId, '선블록');
  assert.deepEqual(rows.map((r) => [r.need_key, r.neg, r.pos]), [['밀림', 93, 38], ['끈적유분', 86, 122]]);
  assert.equal(rows.every((r) => r.month === ''), true);
  // Screen 4: the row count and YouTube values stay the same too.
  const character = needCharacterRows(needFixture, needRunId, '선블록');
  assert.deepEqual(character.map((r) => [r.yt_neg, r.yt_pos]), [[12, 3], [0, 0]]);
  assert.equal(hasYoutubeMentions(needCharacterRows(needFixture, needRunId, '쿠션')), false);
  // Screen 3: the product axis has only that one row.
  assert.deepEqual(productRows(needFixture, needRunId).map((r) => r.product_ref), ['oy:A1']);
  // The scope list does not grow either — a month row never creates a new scope.
  assert.deepEqual(scopesForRun(needFixture, needRunId), ['선블록', '쿠션']);
});

// The month rows #129 produces are attached only to the category sum (product_ref='') — neither the
// all-period row (month='') nor the product-axis row may mix into this axis. Order is ascending by month so left is the past.
test('monthRows: 그 (run·scope·need_key) 의 월 행만 오름차순으로 준다 (#130)', () => {
  const rows = monthRows(needFixture, 2, '선블록', '밀림');
  assert.deepEqual(rows.map((r) => r.month), ['2026-06', '2026-07', '2026-08']);
  assert.deepEqual(rows.map((r) => r.neg), [30, 0, 63]);
  assert.equal(rows.some((r) => r.month === '' || r.product_ref !== ''), false);
  // A different need_key in the same scope only sees its own month rows.
  assert.deepEqual(monthRows(needFixture, 2, '선블록', '끈적유분').map((r) => r.month), ['2026-08']);
  // A different run · a scope that does not exist returns an empty array.
  assert.deepEqual(monthRows(needFixture, 1, '선블록', '밀림'), []);
});

// This codebase's biggest taboo: drawing a fact that does not exist. 2026-07 is a month whose neg is 0,
// and that is a different fact from "a month with no row," so it must stay as a 0 bar — dropping it would turn it into a month that never happened.
test('monthRows: 그 달에 0 건인 행은 남는다 — 월 행이 없는 것과 다르다 (#130)', () => {
  const rows = monthRows(needFixture, 2, '선블록', '밀림');
  assert.deepEqual(rows.filter((r) => r.month === '2026-07').map((r) => r.neg), [0]);
  assert.equal(rows.length, 3);
});

// Drawing all 90 months (measured 2013-08~2026-08) makes the panel 2,500px (the height #122 removed).
// The order must stay ascending even when trimmed — the most recent N months are kept, trimmed from the end.
test('monthRows: limit 은 최근 N 개월만 남기고 순서는 그대로다 (#130)', () => {
  assert.deepEqual(monthRows(needFixture, 2, '선블록', '밀림', 2).map((r) => r.month), ['2026-07', '2026-08']);
  assert.equal(monthRows(needFixture, 2, '선블록', '밀림', 99).length, 3);
  // 0 means no cap — the table shows even the months the panel pushed off.
  assert.deepEqual(monthRows(needFixture, 2, '선블록', '밀림', 0).map((r) => r.month), ['2026-06', '2026-07', '2026-08']);
});

// The source of truth for the cap is screens.js — if app.js held its own number the two could silently drift.
// This is where it is caught whether the default is actually MONTH_LIMIT (#130 fix round).
test('monthRows: 기본 상한은 MONTH_LIMIT 이다 (#130)', () => {
  const many = Array.from({ length: MONTH_LIMIT + 6 }, (_, i) => ({
    run_id: 3, scope: 'a', need_key: 'x', product_ref: '',
    month: `20${String(20 + Math.floor(i / 12)).padStart(2, '0')}-${String((i % 12) + 1).padStart(2, '0')}`,
    neg: i,
  }));
  const rows = monthRows(many, 3, 'a', 'x');
  assert.equal(rows.length, MONTH_LIMIT);
  // The side that gets trimmed is the front (older months); the last one stays the most recent month.
  assert.equal(rows.at(-1).month, many.at(-1).month);
  assert.equal(rows[0].month, many[6].month);
  assert.equal(monthRows(many, 3, 'a', 'x', 0).length, many.length);
});

// "This scope has no month rows" becomes wording and "0 that month" becomes a 0 bar — that fork is
// exactly these two functions (the same distinction hasYoutubeMentions makes on the YouTube axis).
test('monthNeedKeys·hasMonthRows: 월 행이 없는 scope 를 구분한다 (#130)', () => {
  assert.deepEqual(monthNeedKeys(needFixture, 2, '선블록'), ['끈적유분', '밀림']);
  assert.equal(hasMonthRows(needFixture, 2, '선블록'), true);
  // 쿠션은 전체 기간 행만 있다 — 니즈가 없는 것이 아니라 월 축이 아직 없는 것이다.
  assert.deepEqual(monthNeedKeys(needFixture, 2, '쿠션'), []);
  assert.equal(hasMonthRows(needFixture, 2, '쿠션'), false);
  // A run with no month rows at all gets the same wording (the state before #129 has ever run).
  assert.equal(hasMonthRows(needFixture, 1, '선블록'), false);
});

// Mimics the rows PostgREST would return for this spec — filtered, then keeping only the columns
// listed in select (a missing value is NULL, so null). The projection is the key part: the server never puts
// a column absent from select into the JSON, so that key is entirely absent from the response row, and any
// comparison reading it is always false. Feeding the fixture whole into a consuming function would hide that fact, letting it pass even while reading a column that was never fetched
// (the spot #130's first round missed — monthSelect had no product_ref, yet monthRowsOf read it).
// The mimicry stops here: only the eq and neq operators exist, and neq is JS's !==, which differs from real Postgres's
// `<> ''` (NULL stays NULL, so the row drops out). Right now the three specs' month·product_ref
// are always the '' sentinel rather than NULL, so this is harmless, but if a spec ever gains in·gte, or neq is
// applied to a genuinely NULL column, this helper will silently fail to filter — extend it here when that happens.
function served(rows, { select, filters }) {
  return rows
    .filter((r) => (filters || []).every(({ column, op, value }) => (
      op === 'neq' ? r[column] !== value : r[column] === value
    )))
    .map((r) => Object.fromEntries(select.map((c) => [c, c in r ? r[c] : null])));
}

const SERVED = {
  category: served(needFixture, NEED_QUERIES.category),
  product: served(needFixture, NEED_QUERIES.product),
  month: served(needFixture, NEED_QUERIES.month),
};

// The contract between the select list and the consuming function. Dropping one column from the spec turns this red immediately.
test('월 축: 질의가 실제로 돌려주는 행으로도 소비 함수가 돈다 (#130)', () => {
  assert.equal(SERVED.month.length, 4); // the filter picks the four month rows
  assert.equal(hasMonthRows(SERVED.month, 2, '선블록'), true);
  assert.deepEqual(monthNeedKeys(SERVED.month, 2, '선블록'), ['끈적유분', '밀림']);
  assert.deepEqual(
    monthRows(SERVED.month, 2, '선블록', '밀림').map((r) => [r.month, r.neg]),
    [['2026-06', 30], ['2026-07', 0], ['2026-08', 63]],
  );
  // A scope with no month rows still lands on the wording side even after projection — this distinction must survive even when the contract holds.
  assert.equal(hasMonthRows(SERVED.month, 2, '쿠션'), false);
});

// The same regression guard the same way. If someone drops a column from select next time, screens 1·3·4 catch it.
test('화면 1·3·4 도 질의가 돌려주는 행으로 같은 값을 낸다 (#130)', () => {
  const rows = needRowsForScope(SERVED.category, 2, '선블록');
  assert.deepEqual(rows.map((r) => [r.need_key, r.neg, r.pos]), [['밀림', 93, 38], ['끈적유분', 86, 122]]);
  const character = needCharacterRows(SERVED.category, 2, '선블록');
  assert.deepEqual(character.map((r) => [r.yt_neg, r.yt_pos]), [[12, 3], [0, 0]]);
  assert.equal(character[0].persist_month_ratio, 5 / 6);   // persist_* is still present in select
  assert.equal(character[0].low_share, 0.44);
  assert.equal(hasYoutubeMentions(needCharacterRows(SERVED.category, 2, '쿠션')), false);
  assert.deepEqual(productRows(SERVED.product, 2).map((r) => r.product_ref), ['oy:A1']);
  assert.deepEqual(scopesForRun(SERVED.category, 2), ['선블록', '쿠션']);
  // The three partition the fixture without gaps or overlap — an overlap would receive the same row twice.
  assert.equal(SERVED.category.length + SERVED.product.length + SERVED.month.length, needFixture.length);
});
