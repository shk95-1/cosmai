import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { latestRunId, sortRows, topByDimension } from '../public/query.js';
import { renderDivergingBars, renderMagnitudeBars, renderTopBars } from '../public/render.js';

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
