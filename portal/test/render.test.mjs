import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { latestRunId, sortRows, topByDimension } from '../public/query.js';
import {
  renderDivergingBars, renderMagnitudeBars, renderTopBars, renderScatter,
  CHART_W_WIDE, CHART_W_SMALL, cellKind, formatCell, isNumericCell,
} from '../public/render.js';
import { needCharacterRows } from '../public/screens.js';

const here = dirname(fileURLToPath(import.meta.url));
const needFixture = JSON.parse(readFileSync(join(here, 'fixtures/metrics_need.sample.json'), 'utf8'));
const wishFixture = JSON.parse(readFileSync(join(here, 'fixtures/metrics_wish.sample.json'), 'utf8'));

// The same filter app.js applies: latest run · category sum (product_ref='', month='').
function categoryRowsForLatestRun(rows, scope) {
  const runId = latestRunId(rows);
  return rows.filter((r) => r.run_id === runId && r.product_ref === '' && r.month === '' && r.scope === scope);
}

test('metrics_need 픽스처: 최신 run(2)만 카테고리 화면에 남는다', () => {
  const rows = categoryRowsForLatestRun(needFixture, '선블록');
  assert.deepEqual(rows.map((r) => [r.run_id, r.need_key, r.neg]), [[2, '밀림', 93], [2, '끈적유분', 86]]);
});

test('renderDivergingBars: need_key 수만큼 neg/pos 막대 쌍을 그린다', () => {
  const rows = sortRows(categoryRowsForLatestRun(needFixture, '선블록').concat(
    { run_id: 2, need_key: '끈적유분', neg: 3, pos: 9, unresolved: 0.25 },
  ), 'unresolved', 'desc');
  const svg = renderDivergingBars(rows);
  const negBars = svg.match(/class="bar-neg"/g) || [];
  const posBars = svg.match(/class="bar-pos"/g) || [];
  assert.equal(negBars.length, rows.length);
  assert.equal(posBars.length, rows.length);
  assert.match(svg, /밀림/);
  assert.match(svg, /끈적유분/);
});

test('renderDivergingBars escapes need_key text', () => {
  const svg = renderDivergingBars([{ need_key: '<script>', neg: 1, pos: 1 }]);
  assert.doesNotMatch(svg, /<script>/);
  assert.match(svg, /&lt;script&gt;/);
});

test('renderMagnitudeBars: unresolved 은 blue, population_share_pct 는 amber 클래스를 쓴다', () => {
  const rows = categoryRowsForLatestRun(needFixture, '선블록');
  const unresolvedSvg = renderMagnitudeBars(rows, { key: 'unresolved', hue: 'blue' });
  const popSvg = renderMagnitudeBars(rows, { key: 'population_share_pct', hue: 'amber' });
  assert.match(unresolvedSvg, /class="bar-magnitude-1"/);
  assert.match(popSvg, /class="bar-magnitude-2"/);
});

test('renderMagnitudeBars formats values with fmt', () => {
  const svg = renderMagnitudeBars([{ need_key: 'x', population_share_pct: 0.5 }], {
    key: 'population_share_pct', fmt: (v) => `${v.toFixed(2)}%`,
  });
  assert.match(svg, />0\.50%</);
});

test('screen 2: topByDimension + renderTopBars 는 마진 없는 축만 집계한다', () => {
  const runId = latestRunId(wishFixture);
  const rows = wishFixture.filter((r) => r.run_id === runId && r.scope === 'wish:a');
  const top = topByDimension(rows, 'format', 5);
  assert.deepEqual(top, [{ value: '스틱', mentions: 12 }, { value: '스프레이', mentions: 4 }]);
  const svg = renderTopBars(top);
  assert.equal((svg.match(/class="bar-magnitude-1"/g) || []).length, 2);
  assert.match(svg, /스틱/);
});

test('renderTopBars on an empty list renders an empty chart, not a crash', () => {
  const svg = renderTopBars([]);
  assert.match(svg, /<svg/);
  assert.deepEqual(svg.match(/<rect/g), null);
});

// ---- Screen 4: scatter ------------------------------------------------------

const SCATTER = { xKey: 'persist_month_ratio', yKey: 'persist_product_ratio', sizeKey: 'unresolved', xLabel: '지속 월 비율', yLabel: '확산 제품 비율' };

test('renderScatter: 비율이 있는 need_key 만큼 점을 찍는다', () => {
  const rows = needCharacterRows(needFixture, 2, '선블록');
  const svg = renderScatter(rows, SCATTER);
  assert.equal((svg.match(/class="viz-dot"/g) || []).length, 2);
  assert.match(svg, /밀림/);
  assert.match(svg, /끈적유분/);
  assert.match(svg, /지속 월 비율/);
  assert.match(svg, /확산 제품 비율/);
});

// When the denominator (persist_months_total) is 0 there is no ratio — flattening it to 0 and plotting it
// at the bottom left would become the false signal "a need that never persists." That point is never drawn at all.
test('renderScatter: 분모 0 인 행은 점이 되지 않는다', () => {
  const rows = needCharacterRows(needFixture, 2, '쿠션');
  const svg = renderScatter(rows, SCATTER);
  assert.equal(rows.length, 1);
  assert.equal(svg.match(/class="viz-dot"/g), null);
  assert.match(svg, /<svg/);
});

test('renderScatter: 0.5 사분면 구분선을 그린다', () => {
  const svg = renderScatter(needCharacterRows(needFixture, 2, '선블록'), SCATTER);
  assert.equal((svg.match(/class="viz-quadrant"/g) || []).length, 2);
});

test('renderScatter escapes need_key text', () => {
  const rows = [{ need_key: '<script>', persist_month_ratio: 0.5, persist_product_ratio: 0.5, unresolved: 1 }];
  const svg = renderScatter(rows, SCATTER);
  assert.doesNotMatch(svg, /<script>/);
  assert.match(svg, /&lt;script&gt;/);
});

// With many points, labels overlap each other — a label that would overlap an already-placed label box is skipped
// (#122: 7 clustered near y=1 overlapped each other), and every name still remains in <title> (on hover).
// When 20 points overlap in one spot, only the two slots to that point's left and right are free — the third onward
// overlaps an already-placed box on either side, so it is skipped.
test('renderScatter: 한 자리에 몰린 점은 좌우 두 개만 라벨하고 title 은 전부 남는다', () => {
  const rows = Array.from({ length: 20 }, (_, i) => ({
    need_key: `n${i}`, persist_month_ratio: 0.5, persist_product_ratio: 0.5, unresolved: i / 20,
  }));
  const svg = renderScatter(rows, SCATTER);
  assert.equal((svg.match(/class="viz-dot"/g) || []).length, 20);
  assert.equal((svg.match(/<title>/g) || []).length, 20);
  assert.equal((svg.match(/class="viz-point-label"/g) || []).length, 2);
  assert.match(svg, /class="viz-point-label"[^>]*>n19</);
});

// The opposite extreme: points spread apart must all get a label — greedy placement must never drop a spot
// that has no overlap, or the screen would just look empty.
test('renderScatter: 흩어진 점은 전부 라벨을 받는다', () => {
  const rows = [0.1, 0.35, 0.6, 0.85].map((y, i) => ({
    need_key: `n${i}`, persist_month_ratio: 0.2, persist_product_ratio: y, unresolved: 1,
  }));
  const svg = renderScatter(rows, SCATTER);
  assert.equal((svg.match(/class="viz-point-label"/g) || []).length, 4);
});

test('renderScatter: 행이 없어도 축은 그린다', () => {
  const svg = renderScatter([], SCATTER);
  assert.match(svg, /<svg/);
  assert.match(svg, /class="viz-axis"/);
});


// ---- #122 panel width · axis · tooltip · table format --------------------------------------------

// Extracts <rect …/>'s attributes — because whether the coordinate math is right is hard to see via string matching.
function rects(svg) {
  return [...svg.matchAll(/<rect ([^>]*)\/>/g)].map(([, attrs]) => Object.fromEntries(
    [...attrs.matchAll(/([\w-]+)="([^"]*)"/g)].map(([, k, v]) => [k, v]),
  ));
}

const BARS = Array.from({ length: 8 }, (_, i) => ({ need_key: `n${i}`, neg: 10 - i, pos: i, unresolved: 10 - i }));

// #122: when the viewBox width differs from the panel's actual width, svg.viz-root{width:100%} enlarges the
// text by that same ratio too (measured 3.29x). Since width differs by panel, it must be a render option.
test('판 폭은 렌더 옵션이다 — 전체폭 960, 작은 판 420', () => {
  assert.equal(CHART_W_WIDE, 960);
  assert.equal(CHART_W_SMALL, 420);
  assert.match(renderMagnitudeBars(BARS, { key: 'unresolved' }), /viewBox="0 0 420 /);
  assert.match(renderMagnitudeBars(BARS, { key: 'unresolved', width: CHART_W_WIDE }), /viewBox="0 0 960 /);
  assert.match(renderDivergingBars(BARS, { width: CHART_W_WIDE }), /viewBox="0 0 960 /);
  assert.match(renderTopBars([{ value: 'a', mentions: 1 }], { width: CHART_W_WIDE }), /viewBox="0 0 960 /);
});

// A two-row grouped bar had one need_key eating 58px vertically, making the panel 3,323px (#122).
// With neg left / pos right symmetric on one row, the same information fits in half the height.
test('renderDivergingBars: neg 와 pos 가 한 행에서 가운데 축을 두고 마주 본다', () => {
  const svg = renderDivergingBars([{ need_key: 'a', neg: 10, pos: 5 }], { width: CHART_W_WIDE });
  const [neg, pos] = rects(svg);
  assert.equal(neg.class, 'bar-neg');
  assert.equal(pos.class, 'bar-pos');
  assert.equal(neg.y, pos.y); // the same row
  // neg extends left from the axis, pos right — the two bars meet at the axis.
  assert.equal(Number(neg.x) + Number(neg.width), Number(pos.x));
  assert.equal(Number(neg.width), 2 * Number(pos.width)); // 10 to 5
});

test('renderDivergingBars: 높이가 행당 한 줄이다', () => {
  const one = renderDivergingBars([BARS[0]], { width: CHART_W_WIDE });
  const four = renderDivergingBars(BARS.slice(0, 4), { width: CHART_W_WIDE });
  const h = (svg) => Number(/viewBox="0 0 \d+ (\d+)"/.exec(svg)[1]);
  assert.equal(h(four) - h(one), 3 * 28);
});

// If a value label were attached to every bar the panel would become a table — only the top 5 are written, the rest are hover-only (<title>).
test('막대 판: 값 라벨은 상위 5 개까지, <title> 은 막대마다 붙는다', () => {
  for (const svg of [
    renderMagnitudeBars(BARS, { key: 'unresolved' }),
    renderTopBars(BARS.map((r, i) => ({ value: r.need_key, mentions: 8 - i }))),
  ]) {
    assert.equal((svg.match(/class="viz-value"/g) || []).length, 5);
    assert.equal((svg.match(/<title>/g) || []).length, 8);
  }
  // In the diverging bars, "top 5" means 5 need_key rows — writing only one side would read as a missing value.
  const div = renderDivergingBars(BARS, { width: CHART_W_WIDE });
  assert.equal((div.match(/class="viz-value"/g) || []).length, 10);
  assert.equal((div.match(/<title>/g) || []).length, 16); // neg·pos each
});

test('막대 판: 0 과 최대 눈금, 옅은 세로 그리드 하나', () => {
  const svg = renderMagnitudeBars(BARS, { key: 'unresolved', fmt: (v) => v.toFixed(2) });
  assert.match(svg, /class="viz-tick"[^>]*>0</);
  assert.match(svg, /class="viz-tick"[^>]*>10\.00</);
  assert.equal((svg.match(/class="viz-grid"/g) || []).length, 1);
});

test('발산 막대: 눈금은 최대·0·최대 세 개다', () => {
  const svg = renderDivergingBars(BARS, { width: CHART_W_WIDE });
  assert.equal((svg.match(/class="viz-tick"/g) || []).length, 3);
  assert.equal((svg.match(/class="viz-axis"/g) || []).length, 1);
});

// Zero rows leaves an empty panel with only a border (#122: screen 2's format·attribute, screen 4's two).
// The .empty-note screen 4's YouTube panel used was moved into render and is now used by every panel.
test('빈 행 + empty 문구 = 빈 SVG 가 아니라 .empty-note', () => {
  for (const svg of [
    renderTopBars([], { empty: '값 없음' }),
    renderMagnitudeBars([], { key: 'unresolved', empty: '값 없음' }),
    renderDivergingBars([], { empty: '값 없음' }),
  ]) {
    assert.match(svg, /class="empty-note"/);
    assert.doesNotMatch(svg, /<svg/);
  }
  assert.match(renderTopBars([], { empty: '<b>x</b>' }), /&lt;b&gt;/);
});

// ---- Table cell format ------------------------------------------------------------

// app.js used textContent = r[c], so 0.891304347826087 came out verbatim (#122).
// The CSV is the source of truth on raw values, so this format is used on screen only.
test('formatCell: 비율·퍼센트·정수를 컬럼별로 다르게 적는다', () => {
  assert.equal(formatCell('unresolved', 0.891304347826087), '0.89');
  assert.equal(formatCell('low_share', 0.005565862981767), '0.01');
  assert.equal(formatCell('population_share_pct', 0.005565862981767), '0.01%');
  assert.equal(formatCell('neg', 101860), '101,860');
  assert.equal(formatCell('mentions', 12), '12');
  assert.equal(formatCell('need_key', '기타불만'), '기타불만');
  assert.equal(formatCell('product_ref', '101473'), '101473'); // a thousands separator on an id would be a lie
  assert.equal(formatCell('unresolved', null), '');
  assert.equal(formatCell('unresolved', ''), '');
});

test('formatCell: 이름 없는 컬럼의 float 도 새지 않는다', () => {
  assert.equal(formatCell('나중에생긴컬럼', 0.891304347826087), '0.89');
  assert.equal(formatCell('나중에생긴컬럼', 'oy:A1'), 'oy:A1');
});

test('cellKind·isNumericCell: 숫자 셀만 우측 정렬 대상이다', () => {
  assert.equal(cellKind('unresolved'), 'ratio');
  assert.equal(cellKind('population_share_pct'), 'pct');
  assert.equal(cellKind('neg'), 'int');
  assert.equal(cellKind('need_key'), 'text');
  assert.equal(isNumericCell('neg', 4), true);
  assert.equal(isNumericCell('need_key', '밀림'), false);
  assert.equal(isNumericCell('unresolved', null), false);
});


// Screen 3's label is 'brand · product name', overflowing the 96px slot — both the label slot's width and
// the column the tooltip reads are decided by the panel (since the truncated label and the full name live in different columns).
test('renderMagnitudeBars: labelW 로 라벨 자리를 넓히고 titleKey 로 툴팁을 따로 준다', () => {
  const rows = [{ product_short: '메디힐 · 티트…', product: '메디힐 · 티트리 임팩트인 밸런싱 마스크', unresolved: 1 }];
  const svg = renderMagnitudeBars(rows, {
    key: 'unresolved', labelKey: 'product_short', titleKey: 'product', labelW: 240, width: CHART_W_WIDE,
  });
  assert.equal(rects(svg)[0].x, '240');
  assert.match(svg, /<title>메디힐 · 티트리 임팩트인 밸런싱 마스크 — 1<\/title>/);
  assert.match(svg, /class="viz-label">메디힐 · 티트…</);
});

// ---- Screen 5: period (month) axis (#130) --------------------------------------------

// The month axis creates no new renderer — the panel that draws one measure against a label axis is already
// renderMagnitudeBars. Only the label axis is switched from need_key to month.
test('renderMagnitudeBars: labelKey 를 month 로 돌리면 월별 막대가 된다 (#130)', () => {
  const rows = [
    { month: '2026-06', neg: 30 }, { month: '2026-07', neg: 0 }, { month: '2026-08', neg: 63 },
  ];
  const svg = renderMagnitudeBars(rows, { key: 'neg', labelKey: 'month', width: CHART_W_WIDE });
  assert.deepEqual(
    [...svg.matchAll(/class="viz-label">([^<]*)</g)].map(([, m]) => m),
    ['2026-06', '2026-07', '2026-08'],
  );
  // A month with 0 stays as a zero-width bar — dropping the row entirely would read as "that month doesn't exist at all."
  const bars = rects(svg);
  assert.equal(bars.length, 3);
  assert.equal(bars[1].width, '0');
  assert.equal(bars[2].width, String(CHART_W_WIDE - 96 - 56)); // the max value (63) fills the panel
});

// A need with no month rows at all is wording, not an empty panel — if "0 items" and "no month rows"
// looked the same here, the screen would be asserting a fact that does not exist.
test('renderMagnitudeBars: 월 행이 없으면 문구다 (#130)', () => {
  assert.match(
    renderMagnitudeBars([], { key: 'neg', labelKey: 'month', empty: '이 니즈의 월 행이 없음' }),
    /class="empty-note">이 니즈의 월 행이 없음</,
  );
});
