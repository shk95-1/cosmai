// Wiring between screens and network. Judgement-bearing computation (query string · paging · CSV · aggregation) is
// entirely pure functions in query.js/render.js, and here it is only wired to DOM and fetch —
// that is why this file has no tests (the same split as data-portal/public/app.js).
import {
  buildQuery, sortRows, topByDimension, buildFileName, fileBody, rowsToCsv, describeError,
  PAGE_SIZE, nextPageOffset, parseContentRange, NEED_QUERIES, LINEAGE_QUERIES,
} from './query.js';
import {
  reproducible, rewritersAfter, needCellFilters, wishCellFilters, documentFilters,
  groupByDocument, describeMatch,
} from './lineage.js';
import {
  latestRuns, scopesForRun, needRowsForScope, wishRowsForScope, productRows, runCaptionParts,
  needCharacterRows, hasYoutubeMentions, rowsWithValue, defaultScope,
  productNameIndex, withProductNames,
  monthRows, monthNeedKeys, hasMonthRows, MONTH_LIMIT,
} from './screens.js';
import {
  renderDivergingBars, renderMagnitudeBars, renderTopBars, renderScatter,
  CHART_W_WIDE, cellKind, formatCell, isNumericCell,
} from './render.js';

// PostgREST is port 3000 of the host serving this page — nothing to fix even if the machine changes.
const API_BASE = `${window.location.protocol}//${window.location.hostname}:3000`;
// count=exact must be present for the server to carry the total count in Content-Range —
// without it we cannot know the total, so we cannot tell whether it was truncated (#81).
const HEADERS = { 'Accept-Profile': 'needs', Prefer: 'count=exact' };

const $ = (id) => document.getElementById(id);
function showError(text) { $('error').textContent = text || ''; }

// Returns one page's rows together with the Content-Range header — deciding whether it was
// truncated is the caller's job (apiAll), so the header is not hidden.
async function apiPage(path) {
  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, { headers: HEADERS });
  } catch {
    throw new Error(`API 에 연결하지 못했습니다 — 주소: ${API_BASE}`);
  }
  if (!res.ok) {
    let body = {};
    try { body = await res.json(); } catch { /* not a JSON error body */ }
    throw new Error(describeError(body));
  }
  return { rows: await res.json(), range: res.headers.get('content-range') };
}

// Follows the offset to keep receiving a response truncated by PGRST_DB_MAX_ROWS(#81) — reuses
// the PAGE_SIZE/nextPageOffset query.js already provides (no new paging scheme is invented).
async function apiAll(basePath, { select, order, filters }) {
  const rows = [];
  let offset = 0;
  for (;;) {
    const q = buildQuery({ select, filters, order, limit: PAGE_SIZE, offset });
    const page = await apiPage(`${basePath}?${q}`);
    rows.push(...page.rows);
    const next = nextPageOffset(offset, page.range);
    if (next === null) break;
    offset = next;
  }
  return rows;
}

// need comes in three sets — the category-sum rows screen 1·4 use, the product-axis rows screen 3
// uses, and the month rows screen 5 uses. Since #41 added the product rows, receiving them as one
// set means the sum-row screen pulls in 13x its share, and once #129's month rows are stacked on that doubles again — that is why the query is split per axis.
const state = {
  need: [], needProducts: [], needMonths: [], wish: [], productNames: new Map(),
  needRunId: null, wishRunId: null,
  // what lineage (#144) reads: whether a run is reproducible is computed from analysis_run alone,
  // and anon already reads that table -- these rows received at boot are already the evidence, so no extra view was added.
  runs: [],
};

// ---- Tabs --------------------------------------------------------------

for (const btn of document.querySelectorAll('#tabs button')) {
  btn.onclick = () => {
    for (const b of document.querySelectorAll('#tabs button')) b.classList.toggle('on', b === btn);
    for (const s of document.querySelectorAll('.screen')) s.classList.toggle('hidden', s.id !== `screen-${btn.dataset.screen}`);
  };
}

// ---- Screen 1: category needs ---------------------------------------------

function renderNeedScreen(scope) {
  const rows = sortRows(needRowsForScope(state.need, state.needRunId, scope), 'unresolved', 'desc');
  // This is a full-width panel so the width must also be full-width — any mismatch in width enlarges the text too (#122).
  $('need-chart').innerHTML =
    '<div class="legend"><span><span class="swatch neg"></span>불만(neg)</span><span><span class="swatch pos"></span>만족(pos)</span></div>' +
    renderDivergingBars(rows, { width: CHART_W_WIDE, empty: '이 scope 에 니즈 행이 없음' });
  $('need-chart-unresolved').innerHTML = renderMagnitudeBars(rows, {
    key: 'unresolved', hue: 'blue', fmt: (v) => v.toFixed(2), empty: '미해결비를 잴 행이 없음',
  });
  $('need-chart-population').innerHTML = renderMagnitudeBars(rows, {
    key: 'population_share_pct', hue: 'amber', fmt: (v) => `${v.toFixed(2)}%`, empty: '점유율을 잴 행이 없음',
  });
  renderNeedTable(rows);
}

// Draws one table. Cells pass through formatCell so raw floats never show up bare (#122),
// and only numeric cells are right-aligned so digit places line up. The downloadable CSV is the source of truth and is left untouched.
function fillTable(host, cols, rows, onSort, onPick) {
  const table = document.createElement('table');
  if (onPick) table.classList.add('drillable');
  const thead = table.createTHead().insertRow();
  for (const c of cols) {
    const th = document.createElement('th');
    th.textContent = c;
    if (cellKind(c) !== 'text') th.classList.add('num');
    if (onSort) th.onclick = () => onSort(c);
    thead.append(th);
  }
  const tbody = table.createTBody();
  for (const r of rows) {
    const tr = tbody.insertRow();
    // If the picked row is not marked, the lineage section below loses which cell it belongs to on screen.
    if (onPick) tr.onclick = () => { markPicked(tbody, tr); onPick(r); };
    for (const c of cols) {
      const td = tr.insertCell();
      td.textContent = formatCell(c, r[c]);
      if (isNumericCell(c, r[c])) td.classList.add('num');
    }
  }
  host.replaceChildren(table);
}

function markPicked(tbody, tr) {
  for (const other of tbody.rows) other.classList.remove('picked');
  tr.classList.add('picked');
}

function renderNeedTable(rows) {
  const cols = ['need_key', 'neg', 'pos', 'unresolved', 'population_share_pct'];
  fillTable($('need-table'), cols, rows, (c) => renderNeedTable(sortRows(rows, c, 'desc')),
    (r) => openDrill(r, 'need'));
  $('need-table')._rows = rows; // the CSV button picks up whatever sort was drawn last
}

function downloadNeedCsv() {
  const rows = $('need-table')._rows || [];
  const cols = ['scope', 'need_key', 'neg', 'pos', 'unresolved', 'population_share_pct'];
  const text = fileBody(rowsToCsv(rows, cols));
  saveFile(text, buildFileName('need', $('need-scope').value, 'csv', new Date()));
}

// ---- Screen 2: wish ------------------------------------------------------

function renderWishScreen(scope) {
  const rows = wishRowsForScope(state.wish, state.wishRunId, scope);
  // If that axis is entirely empty in this run, the empty panel is left with only a border — only wording
  // distinguishes "0 items" from "this axis was not filled" (run 24: format·attribute empty string in every row).
  $('wish-chart-format').innerHTML = renderTopBars(topByDimension(rows, 'format'), { empty: 'format 값이 없음' });
  $('wish-chart-attribute').innerHTML = renderTopBars(topByDimension(rows, 'attribute'), { empty: 'attribute 값이 없음' });
  $('wish-chart-brand').innerHTML = renderTopBars(topByDimension(rows, 'brand'), { empty: 'brand 값이 없음' });
  // The panel is a top-N approximation per axis, but lineage descends from one cell of metrics_wish — there
  // must be a place on screen to pick that cell (#144). Order is by mentions, capped the same as the panel for the same reason.
  fillTable($('wish-table'), ['format', 'attribute', 'brand', 'mentions'],
    sortRows(rows, 'mentions', 'desc').slice(0, WISH_CELL_LIMIT), undefined,
    (r) => openDrill(r, 'wish'));
}

// Cap for the wish-cell table. Drawing all of them would be hundreds of marginal combinations pushing the panel off-screen.
const WISH_CELL_LIMIT = 30;

// ---- Screen 3: unresolved by product ----------------------------------------

// The label slot (PRODUCT_LABEL_W) and the ellipsis length are a pair — 11px Korean glyphs are about
// 11px per character, so 20 characters fill the 240px slot without overflowing.
const PRODUCT_LABEL_W = 240;
const PRODUCT_LABEL_MAX = 20;

function renderProductScreen() {
  const rows = withProductNames(
    productRows(state.needProducts, state.needRunId, 20), state.productNames, PRODUCT_LABEL_MAX,
  );
  $('product-chart').innerHTML = renderMagnitudeBars(rows, {
    key: 'unresolved', labelKey: 'product_short', titleKey: 'product', hue: 'blue',
    fmt: (v) => v.toFixed(2), width: CHART_W_WIDE, labelW: PRODUCT_LABEL_W, empty: '제품 축 행이 없음',
  });
  // The ref column is kept — rows the linker could not attach have no name, so ref is the only identifier.
  fillTable($('product-table'), ['product', 'product_ref', 'scope', 'need_key', 'neg', 'pos', 'unresolved'],
    rows, undefined, (r) => openDrill(r, 'need'));
}

// ---- Screen 4: need character ------------------------------------------

function renderCharacterScreen(scope) {
  const rows = sortRows(needCharacterRows(state.need, state.needRunId, scope), 'unresolved', 'desc');
  $('character-chart-scatter').innerHTML = renderScatter(rows, {
    xKey: 'persist_month_ratio', yKey: 'persist_product_ratio', sizeKey: 'unresolved',
    xLabel: '지속(월 비율)', yLabel: '확산(제품 비율)',
  });

  // A scope with no YouTube collected reads as a chart of all-zero bars, "neither satisfied nor dissatisfied."
  $('character-chart-source').innerHTML = hasYoutubeMentions(rows)
    ? '<div class="legend"><span><span class="swatch neg"></span>유튜브 불만(yt_neg)</span><span><span class="swatch pos"></span>유튜브 만족(yt_pos)</span></div>'
      + renderDivergingBars(rows, { negKey: 'yt_neg', posKey: 'yt_pos', negLabel: '유튜브 불만', posLabel: '유튜브 만족' })
    : '<p class="empty-note">유튜브 언급 없음</p>';

  const pct = (v) => `${(v * 100).toFixed(1)}%`;
  $('character-chart-new').innerHTML = renderMagnitudeBars(rowsWithValue(rows, 'new_ratio'), {
    key: 'new_ratio', hue: 'blue', fmt: pct, empty: '신규 대비를 잴 행이 없음 (unresolved 가 0)',
  });
  $('character-chart-low').innerHTML = renderMagnitudeBars(rowsWithValue(rows, 'low_share'), {
    key: 'low_share', hue: 'amber', fmt: pct, empty: '저평점 표본이 없음',
  });
}

// ---- Screen 5: period (month) ---------------------------------------------

// A panel can be empty for two reasons, and they are different facts — this need has no month rows at
// all, versus month rows exist but their value is entirely null (no denominator to measure it, screens.js's safeRatio spot).
// Merging them into one phrase would make it impossible to tell which one it is from the screen alone.
const MONTH_EMPTY_NO_ROWS = '이 니즈의 월 행이 없음';
const MONTH_EMPTY_NO_VALUE = {
  neg: '이 니즈의 월 행에 불만 건수가 비어 있음',
  unresolved: '월 행은 있으나 미해결비를 잰 달이 없음 (분모가 없다)',
};

function fillMonthNeedKeys(scope) {
  const keys = monthNeedKeys(state.needMonths, state.needRunId, scope);
  $('month-need').replaceChildren(...keys.map((k) => new Option(k, k)));
}

function renderMonthScreen() {
  const scope = $('month-scope').value;
  // "This scope has no month rows" and "0 items that month" are different facts — the former must be wording.
  // Drawing an empty panel or a 0 bar would have the screen assert a fact the aggregation never produced.
  if (!hasMonthRows(state.needMonths, state.needRunId, scope)) {
    $('month-chart').innerHTML =
      '<p class="empty-note">이 scope 에 월 행이 없음 — 집계가 이 scope 의 월 축을 아직 내지 않았습니다.</p>';
    $('month-table').replaceChildren();
    return;
  }
  const metric = $('month-metric').value;
  const needKey = $('month-need').value;
  // The cap belongs to screens.js — rewriting the number here would let the two copies drift.
  const rows = monthRows(state.needMonths, state.needRunId, scope, needKey);
  // Months with a null value are dropped from the bar (rowsWithValue) — flattening them to a 0 bar would
  // turn an unmeasured month into 0. A month that really is 0 stays and becomes a zero-width bar.
  //
  // The two values never sit on the same panel together so there is nothing for color to distinguish —
  // both use the default sequential color, and digit places pass straight through formatCell (if the same
  // value is written with different digit places on the panel and the table, one screen states two different digit places — render.js's header note).
  $('month-chart').innerHTML = renderMagnitudeBars(rowsWithValue(rows, metric), {
    key: metric, labelKey: 'month', hue: 'blue', fmt: (v) => formatCell(metric, v),
    width: CHART_W_WIDE,
    empty: rows.length === 0 ? MONTH_EMPTY_NO_ROWS : MONTH_EMPTY_NO_VALUE[metric],
  });
  // The table cap is 0 (all) — there must be a place on screen to see months pushed off the panel.
  fillTable($('month-table'), ['month', 'neg', 'pos', 'unresolved', 'yt_neg', 'yt_pos'],
    monthRows(state.needMonths, state.needRunId, scope, needKey, 0), undefined,
    (r) => openDrill(r, 'need'));
}

// The one-line summary stays visible while the full versions·note text collapses into a <details> — leaving it
// open would make the header four lines, pushing the first panel off screen (#122).
function showCaption(needRun, wishRun) {
  const { summary, detail } = runCaptionParts(needRun, wishRun);
  const line = document.createElement('span');
  line.textContent = summary;
  $('run-caption').replaceChildren(line);
  if (!detail) return;
  const box = document.createElement('details');
  const head = document.createElement('summary');
  head.textContent = 'run 상세';
  const body = document.createElement('pre');
  body.textContent = detail;
  box.append(head, body);
  $('run-caption').append(box);
}

// ---- Lineage: from one metrics cell down to the collection (#144) ------------------------------

// All judgement lives in lineage.js's pure functions — this only wires it to DOM and fetch.
// A cell having thousands of mentions is routine, so they are received one page at a time. Receiving them all
// freezes the screen, and truncating silently would make the list length look like it contradicts the cell's number, so the total count is printed alongside it.
// PGRST_DB_MAX_ROWS is 1000, so this value must stay under it (#81).
const DRILL_PAGE = 200;

const escapeHtml = (v) => String(v ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

// The cell currently expanded. "Load more" must continue with the same filter, so the query stays here.
const drill = { filters: null, offset: 0, total: null, rows: [] };

function cellTitle(cell, kind) {
  if (kind === 'wish') {
    const axes = [cell.format, cell.attribute, cell.brand].filter(Boolean).join(' · ');
    return `${cell.scope} · ${axes || '(축 없음)'} — run #${cell.run_id}`;
  }
  const parts = [cell.scope, cell.need_key];
  if (cell.month) parts.push(cell.month);
  if (cell.product_ref) parts.push(cell.product_ref);
  return `${parts.join(' · ')} — run #${cell.run_id}`;
}

// An excerpt is not the full text. Without stating that it was cut, a 120-character fragment reads as the whole thing.
function excerptHtml(text, chars) {
  if (!text) return '<span class="empty-note">—</span>';
  const cut = chars > text.length ? ` <span class="cut">…(전문 ${chars}자)</span>` : '';
  return `<span class="excerpt">${escapeHtml(text)}</span>${cut}`;
}

const MENTION_HEAD = '<tr><th>추출된 문장 (120자 발췌)</th><th>극성</th><th>달</th><th>출처</th>'
  + '<th>원문 (120자 발췌)</th><th>원문 시각</th></tr>';

function mentionRowHtml(m) {
  // need is dissatisfied/satisfied, wish has no such axis and likes take that slot instead — an absent value is not filled in.
  const mark = m.polarity
    ? `<span class="badge">${escapeHtml(m.polarity)}</span>`
    : (m.like_count === null || m.like_count === undefined ? '—' : `♡ ${escapeHtml(m.like_count)}`);
  return `<tr>
    <td>${excerptHtml(m.sentence_excerpt, m.sentence_chars)}</td>
    <td>${mark}</td>
    <td>${escapeHtml(m.month)}</td>
    <td>${escapeHtml([m.site, m.src].filter(Boolean).join(' · '))}</td>
    <td>${m.doc_found ? excerptHtml(m.doc_excerpt, m.doc_chars) : '<span class="empty-note">원문에 닿지 못함</span>'}</td>
    <td>${escapeHtml(m.doc_at ?? '—')}</td>
  </tr>`;
}

function renderMentions() {
  const host = $('drill-mentions');
  if (drill.rows.length === 0) {
    host.innerHTML = '<p class="empty-note">이 칸의 언급을 찾지 못했습니다 — 그 사이 모집단'
      + '(extractor_version)이 바뀌었을 수 있습니다.</p>';
    $('drill-more').hidden = true;
    return;
  }
  host.innerHTML = `<p class="caption">${drill.rows.length} / ${drill.total ?? '?'} 건 — 한 줄을 누르면`
    + ' 그것을 걷은 수집분으로 내려갑니다.</p>'
    + `<table class="drillable"><thead>${MENTION_HEAD}</thead>`
    + `<tbody>${drill.rows.map(mentionRowHtml).join('')}</tbody></table>`;
  const tbody = host.querySelector('tbody');
  [...tbody.rows].forEach((tr, i) => {
    tr.onclick = () => { markPicked(tbody, tr); openCollection(drill.rows[i]); };
  });
  // nextPageOffset is not used here since it is meant to cover the last page by PAGE_SIZE(1000) — this screen's
  // page size is smaller than that, so the function would always answer "done" on the first page.
  $('drill-more').hidden = drill.total === null || drill.rows.length >= drill.total;
}

async function loadMoreMentions() {
  showError('');
  try {
    const q = buildQuery({
      ...LINEAGE_QUERIES.mention, filters: drill.filters, limit: DRILL_PAGE, offset: drill.offset,
    });
    const page = await apiPage(`/mention_lineage?${q}`);
    drill.rows.push(...page.rows);
    drill.offset += page.rows.length;
    const total = parseContentRange(page.range);
    if (total !== null) drill.total = total;
    renderMentions();
  } catch (e) {
    showError(e.message);
  }
}

async function openDrill(cell, kind) {
  $('drill').classList.remove('hidden');
  $('drill-title').textContent = `계보 — ${cellTitle(cell, kind)}`;
  $('drill-collection').replaceChildren();
  $('drill-more').hidden = true;

  // A cell where the trace-back does not hold (contracts/versioning.md reproducibility rule). Not showing the
  // list is the answer here — showing a silently-wrong list is worse than showing none.
  if (!reproducible(state.runs, cell.run_id)) {
    const after = rewritersAfter(state.runs, cell.run_id).map((r) => `#${r.run_id} ${r.note || ''}`.trim());
    $('drill-note').innerHTML = '<p class="banner banner-bad">이 run 뒤에 언급을 다시 쓴 실행이 있다</p>'
      + `<p class="caption">${escapeHtml(after.join(' · ')) || '이 run 을 analysis_run 에서 찾지 못했습니다.'}</p>`
      + '<p class="caption">그 실행이 (src, month) 단위로 언급을 지우고 다시 넣어, 이 칸이 센 모집단은'
      + ' 지금 남아 있지 않습니다 — 시간창도 워터마크도 남지 않아 복원되지 않습니다. 최신 run 의'
      + ' 같은 칸에서 내려가세요.</p>';
    $('drill-mentions').replaceChildren();
    return;
  }
  $('drill-note').innerHTML = '';

  const run = state.runs.find((r) => r.run_id === cell.run_id) || null;
  const filters = kind === 'wish' ? wishCellFilters(cell, run) : needCellFilters(cell, run);
  if (filters.length === 0) {
    $('drill-mentions').innerHTML = '<p class="empty-note">이 run 은 모집단(versions.extractor)을'
      + ' 기록하지 않아 어느 언급을 셌는지 고를 수 없습니다.</p>';
    return;
  }
  drill.filters = filters;
  drill.offset = 0;
  drill.total = null;
  drill.rows = [];
  $('drill-mentions').innerHTML = '<p class="caption">불러오는 중…</p>';
  await loadMoreMentions();
}

const COLLECTION_HEAD = '<tr><th>후보</th><th>수집분</th><th>수집 시각</th><th>상태</th><th>범위</th>'
  + '<th>요청 근거</th></tr>';

function collectionRowHtml(r) {
  // commerce is carried by the fetch_log aggregate, youtube by the panel's size — since the evidence comes from
  // two different tables, filling the missing side with 0 would read as "there were 0 requests."
  const evidence = r.requests !== null && r.requests !== undefined
    ? `${r.requests} req · ${r.ok ?? 0} ok`
    : (r.bytes !== null && r.bytes !== undefined ? `${r.bytes} bytes` : '—');
  return `<tr>
    <td>${escapeHtml(r.candidate_rank ?? '—')}</td>
    <td title="${escapeHtml(r.sample_url ?? '')}">${escapeHtml(r.collection_id ?? '—')}</td>
    <td>${escapeHtml(r.collected_at ?? '—')}</td>
    <td><span class="badge">${escapeHtml(r.status ?? '—')}</span></td>
    <td>${escapeHtml(r.scope_note ?? '—')}</td>
    <td class="num">${escapeHtml(evidence)}</td>
  </tr>`;
}

function groupHtml(g) {
  const head = `<p class="caption">${escapeHtml([g.site, g.doc_parent, g.doc_key].filter(Boolean).join(' / '))}`
    + ` — ${escapeHtml(describeMatch(g.match, g.candidate_count))}</p>`;
  // unknown does not mean there are no rows, it means there is no collection — hiding it would make "could not
  // reach it" and "that document doesn't exist" look the same on screen (user decision 2026-08-27).
  if (g.match === 'unknown') {
    return head + '<p class="empty-note">이 문서를 걷은 수집분을 짚을 수 없습니다 — trend_radar.review'
      + ' 에는 run_id 가 없고 이어 주는 것은 captured_at 뿐인데, 그 시각에 맞는 run 행이 없습니다.</p>';
  }
  return head + `<table><thead>${COLLECTION_HEAD}</thead>`
    + `<tbody>${g.rows.map(collectionRowHtml).join('')}</tbody></table>`;
}

async function openCollection(mention) {
  const host = $('drill-collection');
  const filters = documentFilters(mention);
  if (filters.length === 0) {
    host.innerHTML = '<h3>수집분</h3><p class="empty-note">이 갈래는 원문 표가 없어 수집분까지'
      + ' 내려가지 못합니다 — 자막·블로그, 그리고 사이트를 모르는 위시 리뷰입니다.</p>';
    return;
  }
  host.innerHTML = '<h3>수집분</h3><p class="caption">불러오는 중…</p>';
  showError('');
  try {
    // A document's candidates are at most 5 reviews plus however many comment panels — that fits on one page, so it is all fetched at once.
    const rows = await apiAll('/collection_lineage', { ...LINEAGE_QUERIES.collection, filters });
    const groups = groupByDocument(rows);
    host.innerHTML = '<h3>수집분</h3>' + (groups.length === 0
      ? '<p class="empty-note">이 문서의 행이 없습니다 — 원문이 그 사이 지워졌을 수 있습니다.</p>'
      : groups.map(groupHtml).join(''));
  } catch (e) {
    showError(e.message);
  }
}

// ---- Download -----------------------------------------------------------

function saveFile(text, name) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/csv;charset=utf-8' }));
  const a = document.createElement('a');
  a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

// ---- Boot ----------------------------------------------------------------

function openScope(selectId, scope, render) {
  const select = $(selectId);
  if (select.options.length === 0) return;
  if (scope !== null) select.value = scope;
  render(select.value);
}

async function boot() {
  showError('');
  try {
    // The spec for metrics_need's three axes (category sum · product axis · month axis) is query.js's NEED_QUERIES —
    // this must be a place where a test can pin the exclusivity of the three and the select↔screens.js contract (#130).
    const wishSelect = ['run_id', 'scope', 'format', 'attribute', 'brand', 'mentions'];
    const wishOrder = 'run_id.desc,scope,format,attribute,brand'; // full metrics_wish PK
    // analysis_run: the basis for "latest" (#87) — chosen by finished_at·status, not run_id.
    const runSelect = ['run_id', 'finished_at', 'status', 'versions', 'note'];
    const runOrder = 'run_id.desc';
    // The catalog (#11 input) that turns screen 3's ref into a human-readable name. Rides the same
    // paging path as the other four.
    const productRefSelect = ['product_ref', 'brand', 'name', 'name_norm'];

    const [runs, need, needProducts, needMonths, wish, productRefs] = await Promise.all([
      apiAll('/analysis_run', { select: runSelect, order: runOrder }),
      apiAll('/metrics_need', NEED_QUERIES.category),
      apiAll('/metrics_need', NEED_QUERIES.product),
      apiAll('/metrics_need', NEED_QUERIES.month),
      apiAll('/metrics_wish', { select: wishSelect, order: wishOrder }),
      apiAll('/product_ref', { select: productRefSelect, order: 'product_ref' }),
    ]);
    state.need = need;
    state.needProducts = needProducts;
    state.needMonths = needMonths;
    state.wish = wish;
    state.productNames = productNameIndex(productRefs);
    state.runs = runs;
    const { needRunId, wishRunId, needRun, wishRun } = latestRuns(runs, need, wish);
    state.needRunId = needRunId;
    state.wishRunId = wishRunId;
    showCaption(needRun, wishRun);

    // need·wish are different runs (per-slice seeds) so the scope list is also filled per table
    // — filling both from one table's scope leaves the other table with nothing to pick (fix round 1 finding 2).
    const needScopes = scopesForRun(need, needRunId);
    const wishScopes = scopesForRun(wish, wishRunId);
    $('need-scope').replaceChildren(...needScopes.map((s) => new Option(s, s)));
    $('wish-scope').replaceChildren(...wishScopes.map((s) => new Option(s, s)));
    // Screen 4 sees the same table as screen 1 (category sum), so its scope list is the same.
    $('character-scope').replaceChildren(...needScopes.map((s) => new Option(s, s)));
    // Screen 5's scope list is also the same as screen 1's — filling it with only scopes that have month rows would
    // make it impossible to even pick "this scope has no month axis," erasing that distinction from the screen.
    $('month-scope').replaceChildren(...needScopes.map((s) => new Option(s, s)));
    $('month-limit').textContent = String(MONTH_LIMIT); // the caption's number also comes from the source of truth
    $('need-scope').onchange = () => renderNeedScreen($('need-scope').value);
    $('wish-scope').onchange = () => renderWishScreen($('wish-scope').value);
    $('character-scope').onchange = () => renderCharacterScreen($('character-scope').value);
    $('month-scope').onchange = () => { fillMonthNeedKeys($('month-scope').value); renderMonthScreen(); };
    $('month-need').onchange = renderMonthScreen;
    $('month-metric').onchange = renderMonthScreen;

    // Opens with the scope defaultScope picks, not the first item in the order the select was filled (alphabetical) —
    // otherwise "01 > mask pack > sheet pack" would accidentally become the first screen (#122).
    openScope('need-scope', defaultScope(need, needRunId), renderNeedScreen);
    openScope('wish-scope', defaultScope(wish, wishRunId), renderWishScreen);
    openScope('character-scope', defaultScope(need, needRunId), renderCharacterScreen);
    renderProductScreen();
    // Screen 5 opens on a scope that has month rows — opening with screen 1's default (the scope with the most rows
    // overall) would make a scope with no month axis the first screen, always showing just the wording.
    openScope('month-scope', defaultScope(needMonths, needRunId), (scope) => {
      fillMonthNeedKeys(scope);
      renderMonthScreen();
    });
  } catch (e) {
    $('run-caption').textContent = '';
    showError(e.message);
  }
}

$('need-csv').onclick = downloadNeedCsv;
$('drill-more').onclick = loadMoreMentions;
boot();
