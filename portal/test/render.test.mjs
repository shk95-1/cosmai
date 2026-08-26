import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { latestRunId, sortRows, topByDimension } from '../public/query.js';
import { renderDivergingBars, renderMagnitudeBars, renderTopBars, renderScatter } from '../public/render.js';
import { needCharacterRows } from '../public/screens.js';

const here = dirname(fileURLToPath(import.meta.url));
const needFixture = JSON.parse(readFileSync(join(here, 'fixtures/metrics_need.sample.json'), 'utf8'));
const wishFixture = JSON.parse(readFileSync(join(here, 'fixtures/metrics_wish.sample.json'), 'utf8'));

// app.js 가 하는 것과 같은 필터: 최신 run · 카테고리 합(product_ref='', month='').
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

// ---- 화면 4: 산점도 ------------------------------------------------------

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

// 분모(persist_months_total)가 0 이면 비율이 없다 — 0 으로 눕혀 왼쪽 아래에 찍으면
// "지속되지 않는 니즈"라는 거짓 신호가 된다. 그 점은 아예 그리지 않는다.
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

// 점이 많으면 라벨이 서로 덮는다 — 큰 점 상위 N 개만 글자로 적고, 나머지는 <title>
// (호버)로만 이름을 준다. 순수 함수라 DOM 이벤트를 못 달기 때문이다.
test('renderScatter: 점이 많으면 라벨은 상위 N 개, 이름은 title 로 전부 남는다', () => {
  const rows = Array.from({ length: 20 }, (_, i) => ({
    need_key: `n${i}`, persist_month_ratio: 0.5, persist_product_ratio: 0.5, unresolved: i / 20,
  }));
  const svg = renderScatter(rows, SCATTER);
  assert.equal((svg.match(/class="viz-dot"/g) || []).length, 20);
  assert.equal((svg.match(/<title>/g) || []).length, 20);
  assert.equal((svg.match(/class="viz-point-label"/g) || []).length, 12);
  assert.match(svg, /class="viz-point-label"[^>]*>n19</);
});

test('renderScatter: 행이 없어도 축은 그린다', () => {
  const svg = renderScatter([], SCATTER);
  assert.match(svg, /<svg/);
  assert.match(svg, /class="viz-axis"/);
});
