// Pure functions for assembling PostgREST requests and parsing responses. Since they never touch DOM·fetch
// they are verified directly with node --test (the same reason as data-portal/public/query.js).
// Wiring the screens is app.js's job.

// The same value as PGRST_DB_MAX_ROWS — the cap on one response. The CSV download follows it,
// moving offset by this size and continuing to read.
export const PAGE_SIZE = 1000;

// Builds a query string in PostgREST syntax. The leading '?' is not attached.
// Always attaching order is the key point: moving offset without a sort makes rows duplicate or
// go missing between pages (the DB gives no order guarantee).
//
// A filter with an empty value is dropped by default — this is to keep a select that picked nothing
// from masquerading as a filter. But metrics_need's category-sum rows genuinely have product_ref as an
// empty string, so 'product_ref=eq.' is the only filter that picks them (#109) — that spot declares
// "the empty value is being used as a value" via allowEmpty.
export function buildQuery({ select, filters, order, limit, offset } = {}) {
  const params = new URLSearchParams();
  if (select && select.length > 0) params.append('select', select.join(','));
  for (const f of filters || []) {
    if (!f || !f.column || f.value === undefined || f.value === null) continue;
    if (f.value === '' && !f.allowEmpty) continue;
    params.append(f.column, `${f.op}.${f.value}`);
  }
  if (order) params.append('order', order);
  if (limit !== undefined) params.append('limit', String(limit));
  if (offset !== undefined) params.append('offset', String(offset));
  return params.toString();
}

// metrics_need has three axes — category sum (screen 1·4) · product axis (screen 3) · month axis (screen 5).
// The spec for the three lives here rather than as a local constant in app.js because both of the following are "relations between specs."
//
// One is exclusivity: adding one axis means the other two's filters must narrow along with it. A query
// missing month=eq. after the month rows were added receives double its share (#130, measured 7,219 rows → roughly 14,000).
// The other is the contract select makes with screens.js's consuming functions: PostgREST puts only the
// columns listed in select into the JSON, so if a column the filtering side reads is missing from select, that
// key is entirely absent from the response and the comparison is always false — the screen ends up empty. The
// spec must live in a pure module so a test can check select and the consuming functions against each other in one place (#130 fix round).
//
// order is metrics_need's full PK (001_needs.sql) — run_id alone ties often enough that rows can be
// dropped or duplicated during offset paging (the same reason as this file's header).
const NEED_ORDER = 'run_id.desc,scope,need_key,month,product_ref';

// The single table ops (#139) reads -- needs.pipeline_health. The judgement (freshness·last_run_status) is
// already done by the view, so the screen only receives it and lays it out.
//
// The same rule applies here that select must carry every column the consuming function filters on: PostgREST
// puts only the columns listed in select into the JSON, so if freshness·last_run_status, which ops.js's
// isProblem reads, or arm, which byArm reads, is missing, that comparison is always false and the screen ends up empty (the spot #130 got burned by).
//
// Neither the filter nor the sort is left to the server -- there are only as many rows as declared stages
// (14 right now), so they fit on one page, and order is decided by severity in ops.js's pure functions. Mixing in server sort would split that judgement across two places.
export const OPS_QUERY = {
  select: [
    'stage_key', 'arm', 'dataset', 'enabled', 'expected_interval',
    'last_success_at', 'last_run_at', 'last_run_status', 'overdue_by', 'freshness',
    'requests', 'ok', 'blocked', 'failed', 'p90_ms',
  ],
  order: 'stage_key',
};

// The two the structure map (#142) reads. Edges carry the nodes too (#141 -- not having a node table is the design).
// The stage table is fetched separately for arm and enabled: the picture splits color by arm, and greying
// out a disabled stage needs both of those. Edges alone cannot tell.
//
// Sorting is left to the server -- the goal here is only to keep page boundaries stable, and the picture's
// order is decided by layer in map.js's pure functions.
export const MAP_QUERIES = {
  edge: {
    select: ['from_key', 'from_kind', 'to_key', 'to_kind', 'note'],
    order: 'from_key,to_key',
  },
  stage: {
    select: ['stage_key', 'arm', 'dataset', 'enabled'],
    order: 'stage_key',
  },
  // Puts status on the picture (#143). The judgement is already done by the view, so only two columns need to be fetched --
  // those two are all severity.js, which picks the color, ever looks at.
  health: {
    select: ['stage_key', 'freshness', 'last_run_status'],
    order: 'stage_key',
  },
};

// The two views the lineage drilldown (#144) reads. The filter does not live here — since it is decided by
// which cell was clicked, it is built by lineage.js's pure functions, and this file only carries select and order.
//
// Sorting is left to the server. One cell having more than 1,000 mentions (PGRST_DB_MAX_ROWS) is routine, so
// offset paging is required, and moving offset without a sort makes rows overlap or go missing between pages
// (the same reason as this file's header). mention_id is need_mention·wish_mention's PK, and
// (doc_key, candidate_rank) uniquely points to one document's candidate within collection_lineage.
//
// The same rule applies here that select must carry every column a consuming function reads: PostgREST
// puts only the columns listed in select into the JSON, so if doc_parent, which lineage.js's documentFilters
// reads, or match, which groupByDocument reads, is missing, that comparison is always false and the screen ends up empty (#130).
//
// The source-text columns (sentence·body·text) are not in the view to begin with — only the 120-character excerpt ever goes out (user decision).
export const LINEAGE_QUERIES = {
  mention: {
    select: [
      'kind', 'mention_id', 'src', 'site', 'ref', 'polarity', 'like_count',
      'observed_at', 'month', 'sentence_excerpt', 'sentence_chars',
      'doc_kind', 'doc_parent', 'doc_key', 'doc_found', 'doc_excerpt', 'doc_chars',
      'doc_at', 'doc_rating', 'doc_like_count',
    ],
    order: 'mention_id',
  },
  collection: {
    select: [
      'src', 'site', 'doc_parent', 'doc_key', 'doc_at',
      'match', 'candidate_count', 'candidate_rank',
      'collection_kind', 'collection_id', 'collected_at',
      'started_at', 'finished_at', 'status', 'scope_note',
      'requests', 'ok', 'sample_url', 'bytes',
    ],
    order: 'doc_key,candidate_rank',
  },
};

export const NEED_QUERIES = {
  // Screens 1·4: category-sum rows. product_ref·month are genuinely empty strings, so the two eq.(allowEmpty)
  // filters are the only ones that pick them (#109, #130).
  category: {
    select: [
      'run_id', 'scope', 'need_key', 'month', 'product_ref', 'neg', 'pos', 'unresolved',
      'population_share_pct',
      'yt_neg', 'yt_pos', 'persist_months', 'persist_months_total',
      'persist_products', 'persist_products_total', 'unresolved_new', 'low_share',
      'denom_low', 'denom_site',
    ],
    filters: [
      { column: 'product_ref', op: 'eq', value: '', allowEmpty: true },
      { column: 'month', op: 'eq', value: '', allowEmpty: true },
    ],
    order: NEED_ORDER,
  },
  // Screen 3: product-axis rows only — receiving the sum rows too would fill the top 20 with category rows.
  product: {
    select: ['run_id', 'scope', 'need_key', 'month', 'product_ref', 'neg', 'pos', 'unresolved'],
    filters: [
      { column: 'product_ref', op: 'neq', value: '', allowEmpty: true },
      { column: 'month', op: 'eq', value: '', allowEmpty: true },
    ],
    order: NEED_ORDER,
  },
  // Screen 5: month rows only — the exact complement of the two above. denominator·persist_* are NULL on
  // month rows and are not fetched (#129's decision: there is no such thing as that month's denominator).
  // product_ref's value is always an empty string but it is still fetched — screens.js's monthRowsOf filters
  // on it, and if it were missing from select the response row would have no such key, making that comparison
  // always false and screen 5 would print "no month rows" for every scope (#130 fix round).
  month: {
    select: [
      'run_id', 'scope', 'need_key', 'month', 'product_ref',
      'neg', 'pos', 'unresolved', 'yt_neg', 'yt_pos',
    ],
    filters: [
      { column: 'month', op: 'neq', value: '', allowEmpty: true },
      { column: 'product_ref', op: 'eq', value: '', allowEmpty: true },
    ],
    // In this query product_ref is always empty, so the first four are the PK.
    order: 'run_id.desc,scope,need_key,month',
  },
};

// The total count is what follows the slash in 'Content-Range: 0-999/65646'. '*' means the server
// did not count (meaning Prefer: count=exact was missing), so the count is unknown (null).
export function parseContentRange(header) {
  if (!header) return null;
  const total = String(header).split('/')[1];
  if (total === undefined || total === '*') return null;
  const n = Number(total);
  return Number.isFinite(n) ? n : null;
}

// The number of rows the response actually carried. Counting the CSV by line would drift on a value
// containing a newline, so only this number the server counted and sent is trusted.
export function rangeLength(header) {
  if (!header) return 0;
  const range = String(header).split('/')[0];
  if (range === '*' || !range.includes('-')) return 0;
  const [start, end] = range.split('-').map(Number);
  if (!Number.isFinite(start) || !Number.isFinite(end)) return 0;
  return end - start + 1;
}

// The next page's offset, or null when there is nothing left to fetch. Decided from the row count the
// server actually sent this page (rangeLength) and the total count (parseContentRange) —
// even when it is '*' (count unknown), if this page came back shorter than PAGE_SIZE it is the last page.
export function nextPageOffset(offset, header) {
  const got = rangeLength(header);
  if (got === 0) return null;
  const total = parseContentRange(header);
  const next = offset + got;
  if (total !== null && next >= total) return null;
  if (got < PAGE_SIZE) return null;
  return next;
}

// Appends CSV pages together. From the second page on, the header line is dropped.
export function appendCsvPage(accumulated, page, isFirst) {
  if (isFirst) return page;
  const newline = page.indexOf('\n');
  if (newline === -1) return accumulated;
  const body = page.slice(newline + 1);
  if (body === '') return accumulated;
  const sep = accumulated.endsWith('\n') || accumulated === '' ? '' : '\n';
  return accumulated + sep + body;
}

// #87: since 'analysis_run' entered the anon whitelist, the "latest run" judgement is made by
// screens.js's latestRuns/okRunsByRecency (analysis_run.finished_at·status).
// This function remains only for general spots that need the max run_id (test fixtures, etc).
export function latestRunId(rows) {
  let max = null;
  for (const r of rows || []) {
    const id = Number(r && r.run_id);
    if (Number.isFinite(id) && (max === null || id > max)) max = id;
  }
  return max;
}

// Sorts, most recent first, only the analysis_run rows that finished (status='ok', finished_at present).
// A manually-run aggregate can reuse an existing run via note and end up with a smaller run_id
// (analysis/aggregate/pipeline.py's _run_id), so run_id's magnitude cannot pick "latest" (#87).
export function okRunsByRecency(runs) {
  return (runs || [])
    .filter((r) => r && r.status === 'ok' && r.finished_at)
    .slice()
    .sort((a, b) => new Date(b.finished_at) - new Date(a.finished_at));
}

// Sorts a table on a sortable column such as need_key/product_ref. The original array is not touched.
export function sortRows(rows, key, dir = 'desc') {
  const sign = dir === 'asc' ? 1 : -1;
  return [...(rows || [])].sort((a, b) => {
    const av = a[key];
    const bv = b[key];
    if (av === bv) return 0;
    if (av === null || av === undefined) return 1;
    if (bv === null || bv === undefined) return -1;
    return av > bv ? sign : -sign;
  });
}

// Sums mentions per dimKey (format|attribute|brand) value and returns the top n.
// An empty string (a row where a different axis is filled, meaning this one is not marginal) is treated as
// "no value" for this axis and excluded — avoiding the double-counting in contract check #7 (the cross-tab
// marginal vs. PK conflict) would need a proper marginal query, but the first-cut screen is well served by an approximate top list.
export function topByDimension(rows, dimKey, n = 5) {
  const sums = new Map();
  for (const r of rows || []) {
    const v = r[dimKey];
    if (!v) continue;
    sums.set(v, (sums.get(v) || 0) + Number(r.mentions || 0));
  }
  return [...sums.entries()]
    .map(([value, mentions]) => ({ value, mentions }))
    .sort((a, b) => b.mentions - a.mentions)
    .slice(0, n);
}

// The name of the CSV file the screen downloads — distinguished by screen · scope · time.
export function buildFileName(screen, scope, ext, now) {
  const p = (x) => String(x).padStart(2, '0');
  const stamp =
    `${now.getUTCFullYear()}${p(now.getUTCMonth() + 1)}${p(now.getUTCDate())}` +
    `-${p(now.getUTCHours())}${p(now.getUTCMinutes())}`;
  const scopePart = scope ? `.${scope}` : '';
  return `needs.${screen}${scopePart}.${stamp}.${ext}`;
}

const BOM = '﻿';

// Puts a UTF-8 BOM at the front of the CSV body to save — the only signal that stops Excel from reading it
// with the system codepage (CP949) and mangling the Korean (a file with no BOM is read as CP949).
export function fileBody(text) {
  return text.startsWith(BOM) ? text : BOM + text;
}

// Escapes one value as a CSV field. Wrapped in quotes when it contains a comma·quote·newline.
function csvField(v) {
  if (v === null || v === undefined) return '';
  const s = String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

// Turns a row array into CSV text — the order of columns is the header order.
export function rowsToCsv(rows, columns) {
  const header = columns.map(csvField).join(',');
  const body = (rows || []).map((r) => columns.map((c) => csvField(r[c])).join(','));
  return [header, ...body].join('\n');
}

// Adds a one-line hint to PostgREST errors that come up often. The original text is not erased —
// the hint is only a help; the basis for diagnosis is what the server said.
const ERROR_HINTS = {
  '42501': '권한이 없는 테이블입니다 (익명에 노출되지 않았습니다).',
  '42703': '없는 컬럼입니다.',
  PGRST205: '없는 테이블입니다 — 스키마 선택을 확인하세요.',
};

export function describeError(body) {
  const code = body && body.code ? String(body.code) : '';
  const message = body && body.message ? String(body.message) : '알 수 없는 오류';
  const head = code ? `${code} — ${message}` : message;
  const hint = ERROR_HINTS[code];
  return hint ? `${head}\n${hint}` : head;
}
