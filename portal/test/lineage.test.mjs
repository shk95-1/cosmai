import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  extractorsOf, rewritersAfter, reproducible,
  needCellFilters, wishCellFilters, documentFilters, groupByDocument, describeMatch,
} from '../public/lineage.js';
import { buildQuery, LINEAGE_QUERIES } from '../public/query.js';

// Three analysis_run rows. 26 is the latest aggregate run and 27 is the polarity run that rewrote mentions after it --
// exactly the judgement-clause measurement (cron 05:00 analyze all → 08:00 analyze polarity --missing).
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
  // One extractor_version carries two polarity versions: filtering on polarity too shrinks run 26's neg
  // from 15,452 → 8,685 (-44%), with only 2 of 33 rows matching (judgement-clause measurement).
  assert.deepEqual(extractorsOf(RUNS[2]), ['rule-v2.3', 'slice-suncare']);
  assert.deepEqual(extractorsOf(RUNS[0]), ['rule-v2.3']);
  assert.deepEqual(extractorsOf(null), []);
  assert.deepEqual(extractorsOf({ versions: {} }), []);
});

test('그 run 뒤에 끝난 analyze run 이 있으면 재현 불가다', () => {
  // 25 (polarity) and 26 (all) finished after 24 — 24's population is not recovered.
  assert.deepEqual(rewritersAfter(RUNS, 24).map((r) => r.run_id), [25, 26]);
  assert.equal(reproducible(RUNS, 24), false);
  // Nothing finished after 26 — the current need_mention is exactly the population that run counted.
  assert.deepEqual(rewritersAfter(RUNS, 26), []);
  assert.equal(reproducible(RUNS, 26), true);
});

test('analyze 아닌 run 은 언급을 다시 쓰지 않는다', () => {
  // eval:* · trend-quarter:* never touch need_mention. Counting them would close even a
  // reproducible cell as "a rewriting run exists" on screen.
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

// One metrics_need cell = PK (run_id, scope, need_key, month, product_ref).
const CELL = { run_id: 26, scope: '선케어', need_key: '백탁', month: '', product_ref: '' };

test('카테고리 합 칸은 모집단 + category + need_key 로 좁힌다', () => {
  const f = needCellFilters(CELL, RUNS[2]);
  assert.deepEqual(f, [
    { column: 'kind', op: 'eq', value: 'need' },
    { column: 'extractor_version', op: 'in', value: '("rule-v2.3","slice-suncare")' },
    { column: 'category', op: 'eq', value: '선케어' },
    { column: 'need_key', op: 'eq', value: '백탁' },
  ]);
  // An empty month·product_ref is not a filter — the cell means 'all periods · all products.'
  assert.ok(buildQuery({ filters: f }).includes('need_key=eq.%EB%B0%B1%ED%83%81'));
});

test("scope='all' 칸만 canonical 로 접힌 need_key 를 본다", () => {
  // A17: only the rollup folds synonyms through needs.need_key.canonical. Filtering that cell by the raw
  // need_key would drop the whole set of mentions folded under the canonical name.
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
  // A mention that never reaches a source document (yt_transcript·naver_blog) has nowhere to descend.
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
  // Moving offset without a sort makes rows overlap or go missing between pages (query.js's header).
  assert.equal(LINEAGE_QUERIES.mention.order, 'mention_id');
  assert.equal(LINEAGE_QUERIES.collection.order, 'doc_key,candidate_rank');
  // select must carry every column a consuming function reads (the spot #130 got burned by):
  for (const c of ['src', 'site', 'doc_parent', 'doc_key', 'sentence_excerpt', 'doc_excerpt']) {
    assert.ok(LINEAGE_QUERIES.mention.select.includes(c), c);
  }
  for (const c of ['match', 'candidate_count', 'candidate_rank', 'collection_id']) {
    assert.ok(LINEAGE_QUERIES.collection.select.includes(c), c);
  }
  // The source-text column does not exist to begin with — the view only ever produces the 120-character excerpt (user decision 2026-08-27).
  assert.ok(!LINEAGE_QUERIES.mention.select.includes('sentence'));
  assert.ok(!LINEAGE_QUERIES.mention.select.includes('body'));
});
