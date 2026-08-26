import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  buildQuery, parseContentRange, rangeLength, appendCsvPage, nextPageOffset, latestRunId,
  sortRows, topByDimension, buildFileName, fileBody, rowsToCsv, describeError,
} from '../public/query.js';

test('buildQuery adds select/filters/order/limit/offset', () => {
  const q = buildQuery({
    select: ['a', 'b'],
    filters: [{ column: 'scope', op: 'eq', value: 'sun' }],
    order: 'a.desc',
    limit: 10,
    offset: 20,
  });
  const params = new URLSearchParams(q);
  assert.equal(params.get('select'), 'a,b');
  assert.equal(params.get('scope'), 'eq.sun');
  assert.equal(params.get('order'), 'a.desc');
  assert.equal(params.get('limit'), '10');
  assert.equal(params.get('offset'), '20');
});

test('buildQuery skips empty filter values', () => {
  const q = buildQuery({ filters: [{ column: 'scope', op: 'eq', value: '' }] });
  assert.equal(new URLSearchParams(q).has('scope'), false);
});

// #109: 카테고리 합 행은 product_ref 가 빈 문자열이다 — 'product_ref=eq.' 로만 걸러진다.
// 빈 값을 무조건 버리는 기본 규칙(셀렉트가 안 골라진 상태)과 구분하려고 allowEmpty 를 쓴다.
test('buildQuery keeps an empty filter value when allowEmpty is set (#109)', () => {
  const q = buildQuery({
    select: ['run_id', 'need_key'],
    filters: [{ column: 'product_ref', op: 'eq', value: '', allowEmpty: true }],
    order: 'run_id.desc',
  });
  assert.equal(q.includes('product_ref=eq.&'), true);
  const params = new URLSearchParams(q);
  assert.equal(params.get('product_ref'), 'eq.');
});

// 화면 3(제품 축)은 그 반대편 — 빈 product_ref 를 뺀다.
test('buildQuery builds the product-axis filter product_ref=neq. (#109)', () => {
  const q = buildQuery({ filters: [{ column: 'product_ref', op: 'neq', value: '', allowEmpty: true }] });
  assert.equal(new URLSearchParams(q).get('product_ref'), 'neq.');
});

// #130: 월 행이 얹히면 metrics_need 가 두 배가 된다(실측 7,219행 → 대략 14,000). 기존 두
// 질의는 화면에서 걸러 안 보일 뿐 네트워크로는 다 받으므로, 질의 자체를 month=eq. 로 좁힌다.
// 월 축은 그 반대편(month=neq. · product_ref=eq.)이다 — 세 질의가 서로 겹치지 않는다.
test('buildQuery: 전체 기간 질의는 month=eq. 로 월 행을 뺀다 (#130)', () => {
  const q = buildQuery({
    filters: [
      { column: 'product_ref', op: 'eq', value: '', allowEmpty: true },
      { column: 'month', op: 'eq', value: '', allowEmpty: true },
    ],
  });
  const params = new URLSearchParams(q);
  assert.equal(params.get('product_ref'), 'eq.');
  assert.equal(params.get('month'), 'eq.');
});

test('buildQuery: 월 축 질의는 month=neq. 와 product_ref=eq. 를 함께 건다 (#130)', () => {
  const q = buildQuery({
    select: ['run_id', 'scope', 'need_key', 'month', 'neg', 'pos', 'unresolved', 'yt_neg', 'yt_pos'],
    filters: [
      { column: 'month', op: 'neq', value: '', allowEmpty: true },
      { column: 'product_ref', op: 'eq', value: '', allowEmpty: true },
    ],
    order: 'run_id.desc,scope,need_key,month',
  });
  const params = new URLSearchParams(q);
  assert.equal(params.get('month'), 'neq.');
  assert.equal(params.get('product_ref'), 'eq.');
  // 분모·persist_* 는 월 행에서 NULL 이라 받을 이유가 없다(#129 의 결정).
  assert.equal(params.get('select').includes('denom_'), false);
  assert.equal(params.get('select').includes('persist_'), false);
});

test('parseContentRange reads the total after the slash', () => {
  assert.equal(parseContentRange('0-999/65646'), 65646);
  assert.equal(parseContentRange('0-999/*'), null);
  assert.equal(parseContentRange(null), null);
});

test('rangeLength counts the served rows', () => {
  assert.equal(rangeLength('0-999/*'), 1000);
  assert.equal(rangeLength('0-4/10'), 5);
  assert.equal(rangeLength(null), 0);
});

test('appendCsvPage drops the header on later pages', () => {
  const first = appendCsvPage('', 'a,b\n1,2\n', true);
  const second = appendCsvPage(first, 'a,b\n3,4\n', false);
  assert.equal(second, 'a,b\n1,2\n3,4\n');
});

test('appendCsvPage handles a header-only later page', () => {
  const acc = appendCsvPage('a,b\n1,2', 'a,b\n', false);
  assert.equal(acc, 'a,b\n1,2');
});

test('nextPageOffset: 1080행을 PAGE_SIZE(1000)로 나누면 두 번째 페이지에서 멈춘다', () => {
  assert.equal(nextPageOffset(0, '0-999/1080'), 1000);
  assert.equal(nextPageOffset(1000, '1000-1079/1080'), null);
});

test('nextPageOffset: 총 개수를 모를 때(*)는 짧은 페이지로 마지막을 판단한다', () => {
  assert.equal(nextPageOffset(0, '0-999/*'), 1000);
  assert.equal(nextPageOffset(1000, '1000-1531/*'), null);
});

test('nextPageOffset: 빈 페이지는 더 받을 것이 없다', () => {
  assert.equal(nextPageOffset(1000, '*/0'), null);
});

test('latestRunId returns the max run_id, null when empty', () => {
  assert.equal(latestRunId([{ run_id: 3 }, { run_id: 7 }, { run_id: 1 }]), 7);
  assert.equal(latestRunId([]), null);
});

test('sortRows sorts desc by default without mutating input', () => {
  const rows = [{ x: 1 }, { x: 3 }, { x: 2 }];
  const sorted = sortRows(rows, 'x');
  assert.deepEqual(sorted.map((r) => r.x), [3, 2, 1]);
  assert.deepEqual(rows.map((r) => r.x), [1, 3, 2]);
});

test('sortRows asc and null-last', () => {
  const rows = [{ x: 2 }, { x: null }, { x: 1 }];
  assert.deepEqual(sortRows(rows, 'x', 'asc').map((r) => r.x), [1, 2, null]);
});

test('topByDimension sums mentions per value and takes top n', () => {
  const rows = [
    { format: '스틱', mentions: 3 }, { format: '스틱', mentions: 4 },
    { format: '', mentions: 99 },
    { format: '스프레이', mentions: 5 },
  ];
  const top = topByDimension(rows, 'format', 2);
  assert.deepEqual(top, [{ value: '스틱', mentions: 7 }, { value: '스프레이', mentions: 5 }]);
});

test('buildFileName includes screen, scope, timestamp, ext', () => {
  const now = new Date(Date.UTC(2026, 7, 24, 9, 5));
  assert.equal(buildFileName('need', '선케어', 'csv', now), 'needs.need.선케어.20260824-0905.csv');
  assert.equal(buildFileName('product', '', 'csv', now), 'needs.product.20260824-0905.csv');
});

test('fileBody prefixes a BOM exactly once', () => {
  const once = fileBody('a,b\n1,2');
  assert.equal(once.charCodeAt(0), 0xFEFF);
  assert.equal(fileBody(once), once);
});

test('rowsToCsv escapes commas, quotes, newlines', () => {
  const csv = rowsToCsv([{ a: 'x,y', b: 'has "quote"', c: 'line\nbreak' }], ['a', 'b', 'c']);
  assert.equal(csv, 'a,b,c\n"x,y","has ""quote""","line\nbreak"');
});

test('describeError keeps the server message and adds a known hint', () => {
  assert.match(describeError({ code: '42501', message: 'denied' }), /denied/);
  assert.match(describeError({ code: '42501', message: 'denied' }), /익명에 노출/);
  assert.equal(describeError({}), '알 수 없는 오류');
});
