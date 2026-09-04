// The judgement half of the lineage drilldown — pure functions only. No DOM, no fetch, so portal/test can measure it directly
// (the same split as query/screens/render/ops, tool/checks/js).
//
// Since the two views (needs.mention_lineage · needs.collection_lineage) already carry the answer for
// each stage, this file does only two things: turn one metrics cell into that view's filter, and decide
// whether that cell can currently be traced back.
//
// Why this judgement lives on the screen (#144): it is computed from `analysis_run` alone, and anon already reads that table.
// The rows the metrics page receives at boot are already the evidence, so there is no need to build another
// view that asserts the same fact in two places.

// The one predicate the aggregation uses to pick its population is `extractor_version = ANY(...)`, and that list is
// carried straight into `analysis_run.versions.extractor`, joined by ';' (analysis/aggregate/pipeline.py's _versions).
//
// polarity is never filtered on together: one extractor_version can carry two polarity versions, and filtering on run 26
// shrinks neg from 15,452 to 8,685, with only 2 of 33 rows matching (#144 judgement-clause measurement).
export function extractorsOf(run) {
  const raw = run && run.versions && typeof run.versions === 'object' ? run.versions.extractor : null;
  if (typeof raw !== 'string') return [];
  return raw.split(';').map((v) => v.trim()).filter(Boolean);
}

// Only `analyze` ever rewrites mentions. eval and trend-quarter runs never touch need_mention,
// so counting them too would close even a traceable cell as "a rewriting run exists."
const REWRITER = 'analyze:';

function runWith(runs, runId) {
  return (runs || []).find((r) => r && r.run_id === runId) || null;
}

// analyze runs that finished after this run — if any exist, that cell's population is no longer the current
// need_mention. `analyze polarity` deletes and reinserts per (src, month) (analysis/polarity/pipeline.py's
// NEED_DELETE), leaving neither a time window nor a watermark behind, so it cannot be restored.
export function rewritersAfter(runs, runId) {
  const base = runWith(runs, runId);
  if (!base || !base.finished_at) return [];
  const after = new Date(base.finished_at).getTime();
  return (runs || [])
    .filter((r) => r && r.run_id !== runId && r.finished_at && String(r.note || '').startsWith(REWRITER))
    .filter((r) => new Date(r.finished_at).getTime() > after)
    .sort((a, b) => new Date(a.finished_at) - new Date(b.finished_at));
}

// An unknown run is never treated as reproducible — showing a silently-wrong list is worse than showing none.
export function reproducible(runs, runId) {
  const base = runWith(runs, runId);
  if (!base || !base.finished_at) return false;
  return rewritersAfter(runs, runId).length === 0;
}

// PostgREST's in. list. A quote should never appear in a value (the two formats in versioning.md), but the escape stays in place anyway.
function inList(values) {
  return `(${values.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(',')})`;
}

const ROLLUP_SCOPE = 'all';

// One metrics_need cell = PK (run_id, scope, need_key, month, product_ref). The filter that picks the
// mentions that made that cell is population (extractor_version) + that cell's axes.
//
// An empty month and an empty product_ref are not filters — the cell means 'all periods · all products,' and
// filtering on the empty value would match no mentions at all (a mention has no 'all periods' value).
export function needCellFilters(cell, run) {
  if (!cell) return [];
  const extractors = extractorsOf(run);
  if (extractors.length === 0) return [];
  const filters = [
    { column: 'kind', op: 'eq', value: 'need' },
    { column: 'extractor_version', op: 'in', value: inList(extractors) },
  ];
  // A17: only the scope='all' rollup folds through needs.need_key.canonical. Filtering that cell by the raw
  // need_key would drop the whole set of synonym mentions folded under the canonical name.
  if (cell.scope === ROLLUP_SCOPE) {
    filters.push({ column: 'need_key_rollup', op: 'eq', value: cell.need_key });
  } else {
    filters.push({ column: 'category', op: 'eq', value: cell.scope });
    filters.push({ column: 'need_key', op: 'eq', value: cell.need_key });
  }
  if (cell.month) filters.push({ column: 'month', op: 'eq', value: cell.month });
  if (cell.product_ref) filters.push({ column: 'product_axis', op: 'eq', value: cell.product_ref });
  return filters;
}

// metrics_wish's scope is not a category but a kind of wish (WISH_SCOPES, analysis/aggregate/__init__.py).
const WISH_CLASS = { 'wish:a': 'a', 'wish:b': 'b', 'wish:a:format×attr': 'a' };
const WISH_AXES = [['format', 'format_first'], ['attribute', 'attribute_first'], ['brand', 'brand']];

// One metrics_wish cell = PK (run_id, scope, format, attribute, brand). An empty axis is not a value but a
// marginal, so it is not filtered on — filtering would leave only mentions where that axis is actually empty, and the cell's mentions and list length would no longer match.
export function wishCellFilters(cell, run) {
  if (!cell) return [];
  const wishClass = WISH_CLASS[cell.scope];
  const extractors = extractorsOf(run);
  if (!wishClass || extractors.length === 0) return [];
  const filters = [
    { column: 'kind', op: 'eq', value: 'wish' },
    { column: 'extractor_version', op: 'in', value: inList(extractors) },
    { column: 'wish_class', op: 'eq', value: wishClass },
  ];
  for (const [key, column] of WISH_AXES) {
    if (cell[key]) filters.push({ column, op: 'eq', value: cell[key] });
  }
  return filters;
}

// Only the branches that have a source-document table descend to the collection. yt_transcript, naver_blog,
// and wish reviews with no known site already have no doc_kind in mention_lineage, so there is nowhere to descend here either.
const DRILLABLE = new Set(['review', 'yt_comment']);

export function documentFilters(mention) {
  if (!mention || !DRILLABLE.has(mention.src) || !mention.doc_key) return [];
  const filters = [{ column: 'src', op: 'eq', value: mention.src }];
  if (mention.site) filters.push({ column: 'site', op: 'eq', value: mention.site });
  if (mention.doc_parent) filters.push({ column: 'doc_parent', op: 'eq', value: mention.doc_parent });
  filters.push({ column: 'doc_key', op: 'eq', value: mention.doc_key });
  return filters;
}

const docId = (r) => [r.src, r.site, r.doc_parent, r.doc_key].join(' ');

// A document with several candidates is grouped into one bundle but **not reduced** — the path from a review
// to a collection run is joined only by captured_at, leaving 34 percent with 2-5 candidates and 10 percent unknown
// (#144 measurement). Picking one or hiding it is worse, per the user decision, so this function keeps rows as they are and only fixes their order.
export function groupByDocument(rows) {
  const groups = new Map();
  for (const r of rows || []) {
    const id = docId(r);
    if (!groups.has(id)) {
      groups.set(id, {
        src: r.src, site: r.site, doc_parent: r.doc_parent, doc_key: r.doc_key,
        match: r.match, candidate_count: r.candidate_count, rows: [],
      });
    }
    groups.get(id).rows.push(r);
  }
  for (const g of groups.values()) {
    g.rows.sort((a, b) => (a.candidate_rank || 0) - (b.candidate_rank || 0));
  }
  return [...groups.values()];
}

// The one line the screen reads. The three values are different facts, so there are three wordings.
export function describeMatch(match, count) {
  if (match === 'single') return '수집 run 확정';
  if (match === 'candidate') return `후보 수집 run ${count}개 — 어느 것인지 기록이 없다`;
  return '수집 run 미상 — captured_at 에 맞는 run 행이 없다';
}
