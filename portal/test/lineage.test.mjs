import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  extractorsOf, rewritersAfter, reproducible,
  needCellFilters, wishCellFilters, documentFilters, groupByDocument, describeMatch,
} from '../public/lineage.js';
import { buildQuery, LINEAGE_QUERIES } from '../public/query.js';

// analysis_run 세 줄. 26 이 최신 aggregate run 이고 27 은 그 뒤에 언급을 다시 쓴 polarity run 이다 --
// 판단 절의 실측 그대로(크론 05:00 analyze all → 08:00 analyze polarity --missing).
const RUNS = [
  {
    run_id: 24, finished_at: '2026-08-26T05:12:00Z', status: 'ok',
    note: 'analyze:all product_ref=190', versions: { extractor: 'rule-v2.3', polarity: 'rule-v2.2;llm-ollama-gemma4' },
  },
  {
    run_id: 25, finished_at: '2026-08-26T08:31:00Z', status: 'ok',
    note: 'analyze:polarity:llm-ollama-gemma4 missing=0 need=14432', versions: { extractor: 'rule-v2.3' },
  },
  {
    run_id: 26, finished_at: '2026-08-26T11:54:00Z', status: 'ok',
    note: 'analyze:all product_ref=190', versions: { extractor: 'rule-v2.3;slice-suncare' },
  },
];

test("모집단은 versions->>'extractor' 의 ';' 목록이다 — polarity 는 섞지 않는다", () => {
  // 한 extractor_version 이 polarity 두 판본을 담는다: polarity 로 같이 거르면 run 26 의 neg 가
  // 15,452 → 8,685 (-44%) 로 줄고 33행 중 2행만 맞는다 (판단 절 실측).
  assert.deepEqual(extractorsOf(RUNS[2]), ['rule-v2.3', 'slice-suncare']);
  assert.deepEqual(extractorsOf(RUNS[0]), ['rule-v2.3']);
  assert.deepEqual(extractorsOf(null), []);
  assert.deepEqual(extractorsOf({ versions: {} }), []);
});

test('그 run 뒤에 끝난 analyze run 이 있으면 재현 불가다', () => {
  // 24 뒤에는 25(polarity) 와 26(all) 이 끝났다 — 24 의 모집단은 복원되지 않는다.
  assert.deepEqual(rewritersAfter(RUNS, 24).map((r) => r.run_id), [25, 26]);
  assert.equal(reproducible(RUNS, 24), false);
  // 26 뒤에는 아무것도 없다 — 지금 need_mention 이 그 run 이 센 그 모집단이다.
  assert.deepEqual(rewritersAfter(RUNS, 26), []);
  assert.equal(reproducible(RUNS, 26), true);
});

test('analyze 아닌 run 은 언급을 다시 쓰지 않는다', () => {
  // eval:* · trend-quarter:* 는 need_mention 을 건드리지 않는다. 그것을 세면 화면이 재현
  // 가능한 칸까지 "다시 쓴 실행이 있다" 로 닫아 버린다.
  const runs = [...RUNS, {
    run_id: 27, finished_at: '2026-08-26T12:30:00Z', status: 'ok',
    note: 'eval:polarity:rule-v2.2', versions: { extractor: 'rule-v2.3' },
  }];
  assert.deepEqual(rewritersAfter(runs, 26), []);
  assert.equal(reproducible(runs, 26), true);
});

test('끝나지 않은 run 은 아직 다시 쓴 것이 아니다', () => {
  const runs = [...RUNS, {
    run_id: 27, finished_at: null, status: 'running', note: 'analyze:all', versions: {},
  }];
  assert.deepEqual(rewritersAfter(runs, 26), []);
});

test('알 수 없는 run 은 재현 가능으로 눕히지 않는다', () => {
  assert.equal(reproducible(RUNS, 99), false);
  assert.equal(reproducible([], 26), false);
});

// metrics_need 한 칸 = PK (run_id, scope, need_key, month, product_ref).
const CELL = { run_id: 26, scope: '선케어', need_key: '백탁', month: '', product_ref: '' };

test('카테고리 합 칸은 모집단 + category + need_key 로 좁힌다', () => {
  const f = needCellFilters(CELL, RUNS[2]);
  assert.deepEqual(f, [
    { column: 'kind', op: 'eq', value: 'need' },
    { column: 'extractor_version', op: 'in', value: '("rule-v2.3","slice-suncare")' },
    { column: 'category', op: 'eq', value: '선케어' },
    { column: 'need_key', op: 'eq', value: '백탁' },
  ]);
  // 빈 month·product_ref 는 필터가 아니다 — 그 칸이 '전 기간·전 제품' 이라는 뜻이다.
  assert.ok(buildQuery({ filters: f }).includes('need_key=eq.%EB%B0%B1%ED%83%81'));
});

test("scope='all' 칸만 canonical 로 접힌 need_key 를 본다", () => {
  // A17: 롤업만 needs.need_key.canonical 로 동의어를 접는다. 그 칸을 raw need_key 로 거르면
  // 대표 이름으로 접힌 언급들이 통째로 빠진다.
  const f = needCellFilters({ ...CELL, scope: 'all' }, RUNS[2]);
  assert.ok(!f.some((x) => x.column === 'category'));
  assert.ok(f.some((x) => x.column === 'need_key_rollup' && x.value === '백탁'));
  assert.ok(!f.some((x) => x.column === 'need_key'));
});

test('월 칸과 제품 칸은 축 하나씩을 더 건다', () => {
  const month = needCellFilters({ ...CELL, month: '2026-07' }, RUNS[2]);
  assert.ok(month.some((x) => x.column === 'month' && x.op === 'eq' && x.value === '2026-07'));
  const product = needCellFilters({ ...CELL, product_ref: 'p:라운드랩/자작나무' }, RUNS[2]);
  assert.ok(product.some((x) => x.column === 'product_axis' && x.value === 'p:라운드랩/자작나무'));
});

test('wish 칸은 wish_class 와 채워진 축만 건다 — 빈 축은 marginal 이지 값이 아니다', () => {
  const f = wishCellFilters({ run_id: 26, scope: 'wish:a', format: '스틱', attribute: '', brand: '' }, RUNS[2]);
  assert.ok(f.some((x) => x.column === 'kind' && x.value === 'wish'));
  assert.ok(f.some((x) => x.column === 'wish_class' && x.value === 'a'));
  assert.ok(f.some((x) => x.column === 'format_first' && x.value === '스틱'));
  assert.ok(!f.some((x) => x.column === 'attribute_first'));
  assert.ok(!f.some((x) => x.column === 'brand'));
  assert.deepEqual(wishCellFilters({ scope: 'wish:zz' }, RUNS[2]), []);
});

test('언급 한 줄에서 수집분으로 가는 필터는 문서의 자연키다', () => {
  const mention = {
    src: 'review', site: 'glowpick', doc_parent: 'g:1234', doc_key: 'r:99',
  };
  assert.deepEqual(documentFilters(mention), [
    { column: 'src', op: 'eq', value: 'review' },
    { column: 'site', op: 'eq', value: 'glowpick' },
    { column: 'doc_parent', op: 'eq', value: 'g:1234' },
    { column: 'doc_key', op: 'eq', value: 'r:99' },
  ]);
  // 원문에 못 닿은 언급(yt_transcript·naver_blog)은 내려갈 자리가 없다.
  assert.deepEqual(documentFilters({ src: 'yt_transcript', doc_key: null }), []);
});

test('후보 run 이 여럿이면 여럿 그대로 보인다 — 하나로 찍지 않는다', () => {
  const rows = [
    { src: 'review', site: 'glowpick', doc_parent: 'g:1', doc_key: 'r:1', match: 'candidate', candidate_count: 3, candidate_rank: 1, collection_id: 'a' },
    { src: 'review', site: 'glowpick', doc_parent: 'g:1', doc_key: 'r:1', match: 'candidate', candidate_count: 3, candidate_rank: 3, collection_id: 'c' },
    { src: 'review', site: 'glowpick', doc_parent: 'g:1', doc_key: 'r:1', match: 'candidate', candidate_count: 3, candidate_rank: 2, collection_id: 'b' },
    { src: 'yt_comment', site: 'youtube', doc_parent: 'v1', doc_key: 'c1', match: 'single', candidate_count: 1, candidate_rank: 1, collection_id: 'x' },
  ];
  const groups = groupByDocument(rows);
  assert.equal(groups.length, 2);
  assert.equal(groups[0].match, 'candidate');
  assert.deepEqual(groups[0].rows.map((r) => r.collection_id), ['a', 'b', 'c']);
  assert.equal(groups[1].match, 'single');
});

test('미상은 행이 있고 수집분이 없는 것이지, 행이 없는 것이 아니다', () => {
  const rows = [{
    src: 'review', site: 'glowpick', doc_parent: 'g:2', doc_key: 'r:2',
    match: 'unknown', candidate_count: 0, candidate_rank: null, collection_id: null,
  }];
  const [group] = groupByDocument(rows);
  assert.equal(group.match, 'unknown');
  assert.equal(group.rows.length, 1);
  assert.match(describeMatch('unknown', 0), /미상/);
  assert.match(describeMatch('candidate', 3), /3/);
  assert.match(describeMatch('single', 1), /확정/);
});

test('두 계보 질의는 PGRST_DB_MAX_ROWS 아래에서 이어 읽을 정렬을 갖는다', () => {
  // 정렬 없이 offset 을 옮기면 페이지 사이에서 행이 겹치거나 빠진다(query.js 머리말).
  assert.equal(LINEAGE_QUERIES.mention.order, 'mention_id');
  assert.equal(LINEAGE_QUERIES.collection.order, 'doc_key,candidate_rank');
  // select 가 소비 함수가 보는 컬럼을 빠짐없이 담아야 한다(#130 이 데인 자리):
  for (const c of ['src', 'site', 'doc_parent', 'doc_key', 'sentence_excerpt', 'doc_excerpt']) {
    assert.ok(LINEAGE_QUERIES.mention.select.includes(c), c);
  }
  for (const c of ['match', 'candidate_count', 'candidate_rank', 'collection_id']) {
    assert.ok(LINEAGE_QUERIES.collection.select.includes(c), c);
  }
  // 원문 컬럼은 애초에 없다 — 뷰가 120자 발췌만 낸다(사용자 결정 2026-08-27).
  assert.ok(!LINEAGE_QUERIES.mention.select.includes('sentence'));
  assert.ok(!LINEAGE_QUERIES.mention.select.includes('body'));
});
