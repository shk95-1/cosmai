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

// 점이 많으면 라벨이 서로 덮는다 — 이미 놓인 라벨 상자와 겹치는 라벨은 생략하고
// (#122: y=1 근처에 7 개가 몰려 서로 덮었다) 이름은 <title>(호버)로 전부 남긴다.
// 한 자리에 20 개가 겹치면 그 점의 좌우 두 자리만 비어 있다 — 셋째부터는 어느 쪽에
// 놓아도 이미 놓인 상자와 겹치므로 생략된다.
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

// 반대쪽 극단: 서로 떨어진 점은 전부 라벨을 받아야 한다 — 탐욕 배치가 겹침이 없는
// 자리까지 버리면 화면이 그냥 비어 보인다.
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


// ---- #122 판 폭·축·툴팁·표 포맷 --------------------------------------------

// <rect …/> 의 속성을 뽑는다 — 좌표 계산이 맞는지 문자열 매칭으로 보기 어려워서다.
function rects(svg) {
  return [...svg.matchAll(/<rect ([^>]*)\/>/g)].map(([, attrs]) => Object.fromEntries(
    [...attrs.matchAll(/([\w-]+)="([^"]*)"/g)].map(([, k, v]) => [k, v]),
  ));
}

const BARS = Array.from({ length: 8 }, (_, i) => ({ need_key: `n${i}`, neg: 10 - i, pos: i, unresolved: 10 - i }));

// #122: viewBox 폭이 판의 실제 폭과 다르면 svg.viz-root{width:100%} 가 그 비율만큼
// 글자까지 늘린다(실측 3.29배). 폭은 판마다 다르므로 렌더 옵션이어야 한다.
test('판 폭은 렌더 옵션이다 — 전체폭 960, 작은 판 420', () => {
  assert.equal(CHART_W_WIDE, 960);
  assert.equal(CHART_W_SMALL, 420);
  assert.match(renderMagnitudeBars(BARS, { key: 'unresolved' }), /viewBox="0 0 420 /);
  assert.match(renderMagnitudeBars(BARS, { key: 'unresolved', width: CHART_W_WIDE }), /viewBox="0 0 960 /);
  assert.match(renderDivergingBars(BARS, { width: CHART_W_WIDE }), /viewBox="0 0 960 /);
  assert.match(renderTopBars([{ value: 'a', mentions: 1 }], { width: CHART_W_WIDE }), /viewBox="0 0 960 /);
});

// 두 줄짜리 그룹 막대는 need_key 하나가 세로 58px 을 먹어 판이 3,323px 이 됐다(#122).
// neg 왼쪽 / pos 오른쪽 한 행 대칭이면 같은 정보가 절반 높이에 들어간다.
test('renderDivergingBars: neg 와 pos 가 한 행에서 가운데 축을 두고 마주 본다', () => {
  const svg = renderDivergingBars([{ need_key: 'a', neg: 10, pos: 5 }], { width: CHART_W_WIDE });
  const [neg, pos] = rects(svg);
  assert.equal(neg.class, 'bar-neg');
  assert.equal(pos.class, 'bar-pos');
  assert.equal(neg.y, pos.y); // 같은 행
  // neg 는 축에서 왼쪽으로, pos 는 오른쪽으로 — 두 막대가 축에서 만난다.
  assert.equal(Number(neg.x) + Number(neg.width), Number(pos.x));
  assert.equal(Number(neg.width), 2 * Number(pos.width)); // 10 대 5
});

test('renderDivergingBars: 높이가 행당 한 줄이다', () => {
  const one = renderDivergingBars([BARS[0]], { width: CHART_W_WIDE });
  const four = renderDivergingBars(BARS.slice(0, 4), { width: CHART_W_WIDE });
  const h = (svg) => Number(/viewBox="0 0 \d+ (\d+)"/.exec(svg)[1]);
  assert.equal(h(four) - h(one), 3 * 28);
});

// 값 라벨이 막대마다 붙으면 판이 표가 된다 — 상위 5 개만 적고 나머지는 호버(<title>).
test('막대 판: 값 라벨은 상위 5 개까지, <title> 은 막대마다 붙는다', () => {
  for (const svg of [
    renderMagnitudeBars(BARS, { key: 'unresolved' }),
    renderTopBars(BARS.map((r, i) => ({ value: r.need_key, mentions: 8 - i }))),
  ]) {
    assert.equal((svg.match(/class="viz-value"/g) || []).length, 5);
    assert.equal((svg.match(/<title>/g) || []).length, 8);
  }
  // 발산 막대에서 "상위 5 개"는 need_key 5 행이다 — 한쪽만 적으면 없는 값으로 읽힌다.
  const div = renderDivergingBars(BARS, { width: CHART_W_WIDE });
  assert.equal((div.match(/class="viz-value"/g) || []).length, 10);
  assert.equal((div.match(/<title>/g) || []).length, 16); // neg·pos 각각
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

// 행이 0 이면 테두리만 남은 빈 판이 된다(#122: 화면 2 의 format·attribute, 화면 4 의 둘).
// 화면 4 의 유튜브 판이 쓰던 .empty-note 를 렌더 쪽으로 옮겨 판마다 쓴다.
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

// ---- 표 셀 포맷 ------------------------------------------------------------

// app.js 가 textContent = r[c] 라 0.891304347826087 이 그대로 나왔다(#122).
// CSV 는 원시값이 정본이므로 이 포맷은 화면에만 쓴다.
test('formatCell: 비율·퍼센트·정수를 컬럼별로 다르게 적는다', () => {
  assert.equal(formatCell('unresolved', 0.891304347826087), '0.89');
  assert.equal(formatCell('low_share', 0.005565862981767), '0.01');
  assert.equal(formatCell('population_share_pct', 0.005565862981767), '0.01%');
  assert.equal(formatCell('neg', 101860), '101,860');
  assert.equal(formatCell('mentions', 12), '12');
  assert.equal(formatCell('need_key', '기타불만'), '기타불만');
  assert.equal(formatCell('product_ref', '101473'), '101473'); // id 에 천단위는 거짓말이다
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
