// 화면과 네트워크 배선. 판단이 필요한 계산(쿼리 문자열·페이징·CSV·집계)은 전부
// query.js/render.js 의 순수 함수이고, 여기서는 그것을 DOM 과 fetch 에 엮는다 —
// 그래서 이 파일에는 테스트가 없다(data-portal/public/app.js 와 같은 분리).
import {
  buildQuery, latestRunId, sortRows, topByDimension, buildFileName, fileBody, rowsToCsv, describeError,
} from './query.js';
import { renderDivergingBars, renderMagnitudeBars, renderTopBars } from './render.js';

// PostgREST 는 이 페이지를 서빙한 호스트의 3000 번 — 머신이 바뀌어도 고쳐 쓸 것이 없다.
const API_BASE = `${window.location.protocol}//${window.location.hostname}:3000`;
const HEADERS = { 'Accept-Profile': 'needs' };

const $ = (id) => document.getElementById(id);
function showError(text) { $('error').textContent = text || ''; }

async function api(path) {
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
  return res.json();
}

const state = { need: [], wish: [], products: [], runId: null };

// ---- 탭 --------------------------------------------------------------

for (const btn of document.querySelectorAll('#tabs button')) {
  btn.onclick = () => {
    for (const b of document.querySelectorAll('#tabs button')) b.classList.toggle('on', b === btn);
    for (const s of document.querySelectorAll('.screen')) s.classList.toggle('hidden', s.id !== `screen-${btn.dataset.screen}`);
  };
}

// ---- 화면 1: 카테고리 니즈 ---------------------------------------------

function needRowsForScope(scope) {
  return state.need.filter((r) => r.run_id === state.runId && r.product_ref === '' && r.month === '' && r.scope === scope);
}

function renderNeedScreen(scope) {
  const rows = sortRows(needRowsForScope(scope), 'unresolved', 'desc');
  $('need-chart').innerHTML =
    '<div class="legend"><span><span class="swatch neg"></span>불만(neg)</span><span><span class="swatch pos"></span>만족(pos)</span></div>' +
    renderDivergingBars(rows);
  $('need-chart-unresolved').innerHTML = renderMagnitudeBars(rows, { key: 'unresolved', hue: 'blue', fmt: (v) => v.toFixed(2) });
  $('need-chart-population').innerHTML = renderMagnitudeBars(rows, { key: 'population_share_pct', hue: 'amber', fmt: (v) => `${v.toFixed(2)}%` });
  renderNeedTable(rows);
}

function renderNeedTable(rows) {
  const cols = ['need_key', 'neg', 'pos', 'unresolved', 'population_share_pct'];
  const table = document.createElement('table');
  const thead = table.createTHead().insertRow();
  for (const c of cols) {
    const th = document.createElement('th');
    th.textContent = c;
    th.onclick = () => renderNeedTable(sortRows(rows, c, 'desc'));
    thead.append(th);
  }
  const tbody = table.createTBody();
  for (const r of rows) {
    const tr = tbody.insertRow();
    for (const c of cols) tr.insertCell().textContent = r[c];
  }
  $('need-table').replaceChildren(table);
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
  const rows = state.wish.filter((r) => r.run_id === state.runId && r.scope === scope);
  $('wish-chart-format').innerHTML = renderTopBars(topByDimension(rows, 'format'));
  $('wish-chart-attribute').innerHTML = renderTopBars(topByDimension(rows, 'attribute'));
  $('wish-chart-brand').innerHTML = renderTopBars(topByDimension(rows, 'brand'));
}

// ---- 화면 3: 제품별 미해결 ----------------------------------------------

function renderProductScreen() {
  const rows = sortRows(
    state.need.filter((r) => r.run_id === state.runId && r.product_ref !== ''),
    'unresolved', 'desc',
  ).slice(0, 20);
  $('product-chart').innerHTML = renderMagnitudeBars(rows, { key: 'unresolved', labelKey: 'product_ref', hue: 'blue', fmt: (v) => v.toFixed(2) });

  const cols = ['product_ref', 'scope', 'need_key', 'neg', 'pos', 'unresolved'];
  const table = document.createElement('table');
  const thead = table.createTHead().insertRow();
  for (const c of cols) { const th = document.createElement('th'); th.textContent = c; thead.append(th); }
  const tbody = table.createTBody();
  for (const r of rows) {
    const tr = tbody.insertRow();
    for (const c of cols) tr.insertCell().textContent = r[c];
  }
  $('product-table').replaceChildren(table);
}

// ---- 내려받기 -----------------------------------------------------------

function saveFile(text, name) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/csv;charset=utf-8' }));
  const a = document.createElement('a');
  a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

// ---- 부팅 ----------------------------------------------------------------

async function boot() {
  showError('');
  try {
    const needSelect = ['run_id', 'scope', 'need_key', 'month', 'product_ref', 'neg', 'pos', 'unresolved', 'population_share_pct'];
    const needQ = buildQuery({ select: needSelect, order: 'run_id.desc', limit: 5000 });
    const wishSelect = ['run_id', 'scope', 'format', 'attribute', 'brand', 'mentions'];
    const wishQ = buildQuery({ select: wishSelect, order: 'run_id.desc', limit: 5000 });

    const [need, wish] = await Promise.all([api(`/metrics_need?${needQ}`), api(`/metrics_wish?${wishQ}`)]);
    state.need = need;
    state.wish = wish;
    state.runId = latestRunId(need);
    $('run-caption').textContent = state.runId === null ? '데이터 없음' : `최신 run: #${state.runId}`;

    const scopes = [...new Set(need.filter((r) => r.run_id === state.runId).map((r) => r.scope))].sort();
    for (const sel of [$('need-scope'), $('wish-scope')]) {
      sel.replaceChildren(...scopes.map((s) => new Option(s, s)));
    }
    $('need-scope').onchange = () => renderNeedScreen($('need-scope').value);
    $('wish-scope').onchange = () => renderWishScreen($('wish-scope').value);

    if (scopes.length > 0) {
      renderNeedScreen(scopes[0]);
      renderWishScreen(scopes[0]);
    }
    renderProductScreen();
  } catch (e) {
    $('run-caption').textContent = '';
    showError(e.message);
  }
}

$('need-csv').onclick = downloadNeedCsv;
boot();
