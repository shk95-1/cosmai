// 화면과 네트워크 배선. 판단이 필요한 계산(쿼리 문자열·페이징·CSV·집계)은 전부
// query.js/render.js 의 순수 함수이고, 여기서는 그것을 DOM 과 fetch 에 엮는다 —
// 그래서 이 파일에는 테스트가 없다(data-portal/public/app.js 와 같은 분리).
import {
  buildQuery, sortRows, topByDimension, buildFileName, fileBody, rowsToCsv, describeError,
  PAGE_SIZE, nextPageOffset,
} from './query.js';
import {
  latestRuns, scopesForRun, needRowsForScope, wishRowsForScope, productRows, runCaptionParts,
  needCharacterRows, hasYoutubeMentions, rowsWithValue, defaultScope,
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

// need 는 두 벌이다 — 화면 1·4 가 쓰는 카테고리 합 행과 화면 3 이 쓰는 제품 축 행.
// #41 이 제품 행을 더한 뒤로 한 벌로 받으면 합 행 화면이 제 몫의 13배를 끌어온다.
const state = { need: [], needProducts: [], wish: [], needRunId: null, wishRunId: null };

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
function fillTable(host, cols, rows, onSort) {
  const table = document.createElement('table');
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
    for (const c of cols) {
      const td = tr.insertCell();
      td.textContent = formatCell(c, r[c]);
      if (isNumericCell(c, r[c])) td.classList.add('num');
    }
  }
  host.replaceChildren(table);
}

function renderNeedTable(rows) {
  const cols = ['need_key', 'neg', 'pos', 'unresolved', 'population_share_pct'];
  fillTable($('need-table'), cols, rows, (c) => renderNeedTable(sortRows(rows, c, 'desc')));
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
}

// ---- 화면 3: 제품별 미해결 ----------------------------------------------

function renderProductScreen() {
  const rows = productRows(state.needProducts, state.needRunId, 20);
  $('product-chart').innerHTML = renderMagnitudeBars(rows, {
    key: 'unresolved', labelKey: 'product_ref', hue: 'blue', fmt: (v) => v.toFixed(2),
    width: CHART_W_WIDE, empty: '제품 축 행이 없음',
  });
  fillTable($('product-table'), ['product_ref', 'scope', 'need_key', 'neg', 'pos', 'unresolved'], rows);
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
    // 화면 1·4 가 쓰는 카테고리 합 행. product_ref 가 실제로 빈 문자열이라
    // 'product_ref=eq.'(allowEmpty)가 그것을 고르는 유일한 필터다.
    const needSelect = [
      'run_id', 'scope', 'need_key', 'month', 'product_ref', 'neg', 'pos', 'unresolved',
      'population_share_pct',
      'yt_neg', 'yt_pos', 'persist_months', 'persist_months_total',
      'persist_products', 'persist_products_total', 'unresolved_new', 'low_share',
      'denom_low', 'denom_site',
    ];
    const needFilters = [{ column: 'product_ref', op: 'eq', value: '', allowEmpty: true }];
    // 화면 3 은 제품 축 행만 — 합 행을 같이 받으면 상위 20 이 카테고리로 채워진다.
    const productSelect = ['run_id', 'scope', 'need_key', 'month', 'product_ref', 'neg', 'pos', 'unresolved'];
    const productFilters = [{ column: 'product_ref', op: 'neq', value: '', allowEmpty: true }];
    // order 는 metrics_need 의 PK 전체(001_needs.sql) — run_id 만으로는 동률이 흔해
    // offset 페이징 중 행이 빠지거나 겹칠 수 있다(query.js 상단 주석과 같은 이유).
    const needOrder = 'run_id.desc,scope,need_key,month,product_ref';
    const wishSelect = ['run_id', 'scope', 'format', 'attribute', 'brand', 'mentions'];
    const wishOrder = 'run_id.desc,scope,format,attribute,brand'; // metrics_wish PK 전체
    // analysis_run: "최신"의 근거(#87) — run_id 가 아니라 finished_at·status 로 고른다.
    const runSelect = ['run_id', 'finished_at', 'status', 'versions', 'note'];
    const runOrder = 'run_id.desc';

    const [runs, need, needProducts, wish] = await Promise.all([
      apiAll('/analysis_run', { select: runSelect, order: runOrder }),
      apiAll('/metrics_need', { select: needSelect, filters: needFilters, order: needOrder }),
      apiAll('/metrics_need', { select: productSelect, filters: productFilters, order: needOrder }),
      apiAll('/metrics_wish', { select: wishSelect, order: wishOrder }),
    ]);
    state.need = need;
    state.needProducts = needProducts;
    state.wish = wish;
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
    $('need-scope').onchange = () => renderNeedScreen($('need-scope').value);
    $('wish-scope').onchange = () => renderWishScreen($('wish-scope').value);
    $('character-scope').onchange = () => renderCharacterScreen($('character-scope').value);

    // 셀렉트를 채운 순서(사전순)의 첫 항목이 아니라 defaultScope 가 고른 scope 로 연다 —
    // 그러지 않으면 "01 > 마스크팩 > 시트팩" 이 우연히 첫 화면이 된다(#122).
    openScope('need-scope', defaultScope(need, needRunId), renderNeedScreen);
    openScope('wish-scope', defaultScope(wish, wishRunId), renderWishScreen);
    openScope('character-scope', defaultScope(need, needRunId), renderCharacterScreen);
    renderProductScreen();
  } catch (e) {
    $('run-caption').textContent = '';
    showError(e.message);
  }
}

$('need-csv').onclick = downloadNeedCsv;
boot();
