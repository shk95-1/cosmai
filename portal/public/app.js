// 화면과 네트워크 배선. 판단이 필요한 계산(쿼리 문자열·페이징·CSV·집계)은 전부
// query.js/render.js 의 순수 함수이고, 여기서는 그것을 DOM 과 fetch 에 엮는다 —
// 그래서 이 파일에는 테스트가 없다(data-portal/public/app.js 와 같은 분리).
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

// PostgREST 는 이 페이지를 서빙한 호스트의 3000 번 — 머신이 바뀌어도 고쳐 쓸 것이 없다.
const API_BASE = `${window.location.protocol}//${window.location.hostname}:3000`;
// count=exact 가 있어야 서버가 Content-Range 에 전체 개수를 담아 보낸다 —
// 없으면 총량을 몰라 잘렸는지(#81) 판단할 수 없다.
const HEADERS = { 'Accept-Profile': 'needs', Prefer: 'count=exact' };

const $ = (id) => document.getElementById(id);
function showError(text) { $('error').textContent = text || ''; }

// 한 페이지를 받아 rows 와 Content-Range 헤더를 함께 돌려준다 — 잘림 판단은
// 호출자(apiAll)의 몫이라 헤더를 감추지 않는다.
async function apiPage(path) {
  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, { headers: HEADERS });
  } catch {
    throw new Error(`API 에 연결하지 못했습니다 — 주소: ${API_BASE}`);
  }
  if (!res.ok) {
    let body = {};
    try { body = await res.json(); } catch { /* JSON 아닌 오류 본문 */ }
    throw new Error(describeError(body));
  }
  return { rows: await res.json(), range: res.headers.get('content-range') };
}

// PGRST_DB_MAX_ROWS(#81)에 잘린 응답을 offset 을 옮겨 이어 받는다 — query.js 가
// 이미 내놓은 PAGE_SIZE/nextPageOffset 을 그대로 쓴다(새 페이징 방식을 만들지 않는다).
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

// need 는 세 벌이다 — 화면 1·4 가 쓰는 카테고리 합 행, 화면 3 이 쓰는 제품 축 행, 화면 5 가
// 쓰는 월 행. #41 이 제품 행을 더한 뒤로 한 벌로 받으면 합 행 화면이 제 몫의 13배를 끌어오고,
// #129 의 월 행이 얹히면 그것이 다시 두 배가 된다 — 축마다 질의를 나누는 이유다.
const state = {
  need: [], needProducts: [], needMonths: [], wish: [], productNames: new Map(),
  needRunId: null, wishRunId: null,
  // 계보(#144)가 읽는 것: 재현 가능 여부는 analysis_run 만으로 계산되고 anon 이 그 표를 이미
  // 읽는다 -- 부팅에서 받은 이 행들이 그대로 근거라 뷰를 하나 더 두지 않았다.
  runs: [],
};

// ---- 탭 --------------------------------------------------------------

for (const btn of document.querySelectorAll('#tabs button')) {
  btn.onclick = () => {
    for (const b of document.querySelectorAll('#tabs button')) b.classList.toggle('on', b === btn);
    for (const s of document.querySelectorAll('.screen')) s.classList.toggle('hidden', s.id !== `screen-${btn.dataset.screen}`);
  };
}

// ---- 화면 1: 카테고리 니즈 ---------------------------------------------

function renderNeedScreen(scope) {
  const rows = sortRows(needRowsForScope(state.need, state.needRunId, scope), 'unresolved', 'desc');
  // 전체폭 자리의 판이라 폭도 전체폭이어야 한다 — 폭이 어긋난 만큼 글자까지 확대된다(#122).
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

// 표 하나를 그린다. 셀은 formatCell 을 거쳐 원시 float 이 그대로 나오지 않게 하고(#122),
// 숫자 셀만 우측 정렬해 자리수가 눈에 맞는다. 내려받는 CSV 는 원시값이 정본이라 손대지 않는다.
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
    // 고른 줄을 표시해 두지 않으면 아래 계보 절이 어느 칸의 것인지 화면에서 사라진다.
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
  $('need-table')._rows = rows; // CSV 버튼이 마지막으로 그려진 정렬을 그대로 받는다
}

function downloadNeedCsv() {
  const rows = $('need-table')._rows || [];
  const cols = ['scope', 'need_key', 'neg', 'pos', 'unresolved', 'population_share_pct'];
  const text = fileBody(rowsToCsv(rows, cols));
  saveFile(text, buildFileName('need', $('need-scope').value, 'csv', new Date()));
}

// ---- 화면 2: 위시 ------------------------------------------------------

function renderWishScreen(scope) {
  const rows = wishRowsForScope(state.wish, state.wishRunId, scope);
  // 이 run 에서 그 축이 통째로 비면 빈 판이 테두리만 남는다 — 문구라야 "0 건"과
  // "그 축을 못 채웠다"가 구분된다(run 24: format·attribute 가 전 행에서 빈 문자열).
  $('wish-chart-format').innerHTML = renderTopBars(topByDimension(rows, 'format'), { empty: 'format 값이 없음' });
  $('wish-chart-attribute').innerHTML = renderTopBars(topByDimension(rows, 'attribute'), { empty: 'attribute 값이 없음' });
  $('wish-chart-brand').innerHTML = renderTopBars(topByDimension(rows, 'brand'), { empty: 'brand 값이 없음' });
  // 판은 축별 상위 근사이지만 계보는 metrics_wish 의 한 칸에서 내려간다 — 그 칸을 고를 자리가
  // 화면에 하나는 있어야 한다(#144). 순서는 mentions 이고 상한은 판과 같은 이유로 둔다.
  fillTable($('wish-table'), ['format', 'attribute', 'brand', 'mentions'],
    sortRows(rows, 'mentions', 'desc').slice(0, WISH_CELL_LIMIT), undefined,
    (r) => openDrill(r, 'wish'));
}

// 위시 칸 표의 상한. 전부 그리면 marginal 조합이 수백 줄이라 판이 화면 밖으로 밀린다.
const WISH_CELL_LIMIT = 30;

// ---- 화면 3: 제품별 미해결 ----------------------------------------------

// 라벨 자리(PRODUCT_LABEL_W)와 말줄임 길이는 한 쌍이다 — 11px 한글이 글자당 약 11px 이라
// 20 자면 240px 자리를 채우고 넘지 않는다.
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
  // ref 칼럼은 남긴다 — 링커가 못 붙인 행은 이름이 없어 ref 가 유일한 식별자다.
  fillTable($('product-table'), ['product', 'product_ref', 'scope', 'need_key', 'neg', 'pos', 'unresolved'],
    rows, undefined, (r) => openDrill(r, 'need'));
}

// ---- 화면 4: 니즈의 성격 ------------------------------------------------

function renderCharacterScreen(scope) {
  const rows = sortRows(needCharacterRows(state.need, state.needRunId, scope), 'unresolved', 'desc');
  $('character-chart-scatter').innerHTML = renderScatter(rows, {
    xKey: 'persist_month_ratio', yKey: 'persist_product_ratio', sizeKey: 'unresolved',
    xLabel: '지속(월 비율)', yLabel: '확산(제품 비율)',
  });

  // 유튜브를 안 모은 scope 는 0 막대만 늘어선 차트가 되어 "만족도 불만도 없다"로 읽힌다.
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

// ---- 화면 5: 기간(월) ---------------------------------------------------

// 판이 비는 이유는 둘이고 그 둘은 다른 사실이다 — 이 니즈에 월 행이 아예 없는 것과, 월 행은
// 있는데 그 값이 전부 null 인 것(분모가 없어 못 잰 달, screens.js 의 safeRatio 자리).
// 한 문구로 묶으면 나중에 판이 비었을 때 어느 쪽인지 화면만 보고는 알 수 없다.
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
  // "이 scope 에 월 행이 없다"와 "그 달에 0 건"은 다른 사실이다 — 앞은 문구라야 한다.
  // 빈 판이나 0 막대로 그리면 화면이 집계가 내지 않은 사실을 주장한다.
  if (!hasMonthRows(state.needMonths, state.needRunId, scope)) {
    $('month-chart').innerHTML =
      '<p class="empty-note">이 scope 에 월 행이 없음 — 집계가 이 scope 의 월 축을 아직 내지 않았습니다.</p>';
    $('month-table').replaceChildren();
    return;
  }
  const metric = $('month-metric').value;
  const needKey = $('month-need').value;
  // 상한은 screens.js 가 갖는다 — 여기서 숫자를 다시 적으면 두 벌이 어긋난다.
  const rows = monthRows(state.needMonths, state.needRunId, scope, needKey);
  // 값이 null 인 달은 막대에서 뺀다(rowsWithValue) — 0 막대로 눕히면 못 잰 달이 0 이 된다.
  // 0 인 달은 그대로 남아 폭 0 막대가 된다.
  //
  // 두 값이 한 판에 같이 서는 일이 없어 색이 구분할 것이 없다 — 둘 다 기본 순차색이고,
  // 자리수는 formatCell 을 그대로 쓴다(같은 값을 판과 표에서 다르게 적으면 한 화면이
  // 두 가지 자리수를 말한다 — render.js 머리말).
  $('month-chart').innerHTML = renderMagnitudeBars(rowsWithValue(rows, metric), {
    key: metric, labelKey: 'month', hue: 'blue', fmt: (v) => formatCell(metric, v),
    width: CHART_W_WIDE,
    empty: rows.length === 0 ? MONTH_EMPTY_NO_ROWS : MONTH_EMPTY_NO_VALUE[metric],
  });
  // 표는 상한 0(전부) — 판에서 밀린 달을 볼 자리가 화면에 하나는 있어야 한다.
  fillTable($('month-table'), ['month', 'neg', 'pos', 'unresolved', 'yt_neg', 'yt_pos'],
    monthRows(state.needMonths, state.needRunId, scope, needKey, 0), undefined,
    (r) => openDrill(r, 'need'));
}

// 한 줄 요약은 그대로 보이고 versions·note 전문은 <details> 로 접는다 — 펴 두면
// 헤더가 네 줄이 되어 첫 판이 화면 밖으로 밀린다(#122).
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

// ---- 계보: 지표 한 칸에서 수집분까지 (#144) ------------------------------

// 판단은 전부 lineage.js 의 순수 함수다 — 여기는 그것을 DOM 과 fetch 에 엮는다.
// 한 칸의 언급이 수천 건인 것이 예사라 한 쪽씩 받는다. 다 받으면 화면이 굳고, 잘라 놓고 아무 말
// 없이 두면 목록 길이가 칸의 숫자를 반박하는 것처럼 보이므로 전체 개수를 나란히 적는다.
// PGRST_DB_MAX_ROWS 가 1000 이라 이 값은 그 아래여야 한다(#81).
const DRILL_PAGE = 200;

const escapeHtml = (v) => String(v ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

// 지금 펼쳐 둔 칸. 더 보기가 같은 필터로 이어 읽어야 하므로 질의가 여기 남는다.
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

// 발췌는 본문이 아니다. 잘렸다는 사실을 적지 않으면 120자짜리 조각이 전문처럼 읽힌다.
function excerptHtml(text, chars) {
  if (!text) return '<span class="empty-note">—</span>';
  const cut = chars > text.length ? ` <span class="cut">…(전문 ${chars}자)</span>` : '';
  return `<span class="excerpt">${escapeHtml(text)}</span>${cut}`;
}

const MENTION_HEAD = '<tr><th>추출된 문장 (120자 발췌)</th><th>극성</th><th>달</th><th>출처</th>'
  + '<th>원문 (120자 발췌)</th><th>원문 시각</th></tr>';

function mentionRowHtml(m) {
  // need 는 불만/만족, wish 는 그 축이 없고 좋아요가 그 자리다 — 없는 값을 채우지 않는다.
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
  // nextPageOffset 은 PAGE_SIZE(1000) 로 마지막 쪽을 가리므로 여기서는 쓰지 않는다 — 이 화면의
  // 쪽 크기는 그보다 작아서, 그 함수는 첫 쪽에서 언제나 "끝" 이라고 답한다.
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

  // 되짚기가 성립하지 않는 칸(contracts/versioning.md 재현 규칙). 목록을 보이지 않는 것이 이
  // 자리의 답이다 — 조용히 틀린 목록을 보이는 것이 안 보이는 것보다 나쁘다.
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
  // commerce 는 fetch_log 집계가, youtube 는 판의 크기가 그 자리를 진다 — 두 갈래의 근거가
  // 다른 표라서, 없는 쪽을 0 으로 채우면 "요청이 0건이었다" 로 읽힌다.
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
  // 미상은 행이 없는 것이 아니라 수집분이 없는 것이다. 숨기면 "못 닿았다" 와 "그 문서가 없다" 가
  // 화면에서 같아 보인다(사용자 결정 2026-08-27).
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
    // 한 문서의 후보는 리뷰가 최대 5, 댓글이 판 몇 개다 — 한 쪽에 들어오므로 그냥 다 받는다.
    const rows = await apiAll('/collection_lineage', { ...LINEAGE_QUERIES.collection, filters });
    const groups = groupByDocument(rows);
    host.innerHTML = '<h3>수집분</h3>' + (groups.length === 0
      ? '<p class="empty-note">이 문서의 행이 없습니다 — 원문이 그 사이 지워졌을 수 있습니다.</p>'
      : groups.map(groupHtml).join(''));
  } catch (e) {
    showError(e.message);
  }
}

// ---- 내려받기 -----------------------------------------------------------

function saveFile(text, name) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/csv;charset=utf-8' }));
  const a = document.createElement('a');
  a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

// ---- 부팅 ----------------------------------------------------------------

function openScope(selectId, scope, render) {
  const select = $(selectId);
  if (select.options.length === 0) return;
  if (scope !== null) select.value = scope;
  render(select.value);
}

async function boot() {
  showError('');
  try {
    // metrics_need 의 세 축(카테고리 합·제품 축·월 축) 스펙은 query.js 의 NEED_QUERIES 다 —
    // 셋의 배타성과 select↔screens.js 의 계약을 테스트가 잡을 수 있는 자리여야 한다(#130).
    const wishSelect = ['run_id', 'scope', 'format', 'attribute', 'brand', 'mentions'];
    const wishOrder = 'run_id.desc,scope,format,attribute,brand'; // metrics_wish PK 전체
    // analysis_run: "최신"의 근거(#87) — run_id 가 아니라 finished_at·status 로 고른다.
    const runSelect = ['run_id', 'finished_at', 'status', 'versions', 'note'];
    const runOrder = 'run_id.desc';
    // 화면 3 의 ref 를 사람이 읽는 이름으로 바꾸는 카탈로그(#11 입력). 다른 넷과 같은
    // 페이징 경로를 그대로 탄다.
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

    // need·wish는 다른 run(슬라이스별 시드)이라 scope 목록도 표마다 따로 채운다
    // — 한쪽 표의 scope로 둘 다 채우면 다른 표는 고를 값이 없다(수정 라운드 1 결함 2).
    const needScopes = scopesForRun(need, needRunId);
    const wishScopes = scopesForRun(wish, wishRunId);
    $('need-scope').replaceChildren(...needScopes.map((s) => new Option(s, s)));
    $('wish-scope').replaceChildren(...wishScopes.map((s) => new Option(s, s)));
    // 화면 4 는 화면 1 과 같은 표(카테고리 합)를 보므로 scope 목록도 같다.
    $('character-scope').replaceChildren(...needScopes.map((s) => new Option(s, s)));
    // 화면 5 의 scope 목록도 화면 1 과 같다 — 월 행이 있는 scope 만 담으면 "이 scope 에는
    // 월 축이 없다"는 사실을 고를 수조차 없어 그 구별이 화면에서 사라진다.
    $('month-scope').replaceChildren(...needScopes.map((s) => new Option(s, s)));
    $('month-limit').textContent = String(MONTH_LIMIT); // 캡션의 숫자도 정본에서 온다
    $('need-scope').onchange = () => renderNeedScreen($('need-scope').value);
    $('wish-scope').onchange = () => renderWishScreen($('wish-scope').value);
    $('character-scope').onchange = () => renderCharacterScreen($('character-scope').value);
    $('month-scope').onchange = () => { fillMonthNeedKeys($('month-scope').value); renderMonthScreen(); };
    $('month-need').onchange = renderMonthScreen;
    $('month-metric').onchange = renderMonthScreen;

    // 셀렉트를 채운 순서(사전순)의 첫 항목이 아니라 defaultScope 가 고른 scope 로 연다 —
    // 그러지 않으면 "01 > 마스크팩 > 시트팩" 이 우연히 첫 화면이 된다(#122).
    openScope('need-scope', defaultScope(need, needRunId), renderNeedScreen);
    openScope('wish-scope', defaultScope(wish, wishRunId), renderWishScreen);
    openScope('character-scope', defaultScope(need, needRunId), renderCharacterScreen);
    renderProductScreen();
    // 화면 5 는 월 행이 있는 scope 로 연다 — 화면 1 의 기본(전체 행이 가장 많은 scope)으로
    // 열면 월 축이 없는 scope 가 첫 화면이 되어 늘 문구만 보인다.
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
