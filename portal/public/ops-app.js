// Wiring for the ops screen. Judgement-bearing computation is entirely pure functions in ops.js/query.js, and here
// it is only wired to DOM and fetch — the same split as app.js, so this file has no tests.
//
// Why boot is split from the metrics page (index.html): opening the ops page alone must not also pull in the
// three metrics_need sets. All this page fetches is the single pipeline_health table (#139).
import { buildQuery, PAGE_SIZE, nextPageOffset, describeError, OPS_QUERY } from './query.js';
import { problems, problemCount, byArm, disabled, relativeTime, describeInterval } from './ops.js';
import { severityClass } from './severity.js';

const API_BASE = `${window.location.protocol}//${window.location.hostname}:3000`;
const HEADERS = { 'Accept-Profile': 'needs', Prefer: 'count=exact' };
const $ = (id) => document.getElementById(id);

async function apiAll(basePath, { select, order }) {
  const rows = [];
  let offset = 0;
  for (;;) {
    const q = buildQuery({ select, order, limit: PAGE_SIZE, offset });
    let res;
    try {
      res = await fetch(`${API_BASE}${basePath}?${q}`, { headers: HEADERS });
    } catch {
      throw new Error(`API 에 연결하지 못했습니다 — 주소: ${API_BASE}`);
    }
    if (!res.ok) {
      let body = {};
      try { body = await res.json(); } catch { /* not a JSON error body */ }
      throw new Error(describeError(body));
    }
    rows.push(...(await res.json()));
    const next = nextPageOffset(offset, res.headers.get('content-range'));
    if (next === null) break;
    offset = next;
  }
  return rows;
}

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function statsOf(row) {
  if (row.requests === null || row.requests === undefined) return '—';
  const parts = [`${row.requests} req`, `${row.ok ?? 0} ok`];
  if (row.blocked) parts.push(`${row.blocked} blocked`);
  if (row.failed) parts.push(`${row.failed} failed`);
  if (row.p90_ms) parts.push(`p90 ${row.p90_ms}ms`);
  return parts.join(' · ');
}

function rowHtml(row, now) {
  // The absolute time is kept in the title — the main text must be relative so a person does not have to work
  // out whether it is late. The reference TZ is decided by #89, and this spot changes with it then.
  const when = relativeTime(row.last_success_at, now);
  return `<tr class="${severityClass(row)}">
    <td>${esc(row.arm)} · ${esc(row.dataset)}</td>
    <td>${esc(describeInterval(row.expected_interval))}</td>
    <td title="${esc(row.last_success_at ?? '성공한 run 이 없음')}">${esc(when)}</td>
    <td><span class="badge">${esc(row.freshness)}</span></td>
    <td><span class="badge">${esc(row.last_run_status ?? '—')}</span></td>
    <td class="num">${esc(statsOf(row))}</td>
  </tr>`;
}

const HEAD = '<tr><th>단계</th><th>주기</th><th>마지막 성공</th><th>신선도</th><th>마지막 run</th><th>요청</th></tr>';
const table = (rows, now) => `<table><thead>${HEAD}</thead><tbody>${rows.map((r) => rowHtml(r, now)).join('')}</tbody></table>`;

function render(rows, now) {
  const bad = problems(rows);
  const count = problemCount(rows);
  $('ops-banner').innerHTML = count === 0
    ? `<p class="banner banner-ok">${rows.length}개 단계 모두 정상</p>`
    : `<p class="banner banner-bad">막힌 단계 ${count}개</p>`;

  // If there are no problems, the whole section disappears.
  $('ops-problems').innerHTML = count === 0 ? '' : `<h2>지금 볼 것</h2>${table(bad, now)}`;

  $('ops-arms').innerHTML = byArm(rows)
    .map((g) => `<h2>${esc(g.arm)}</h2>${table(g.rows, now)}`)
    .join('');

  const off = disabled(rows);
  $('ops-disabled').innerHTML = off.length === 0
    ? ''
    : `<h2>선언상 안 도는 것</h2><p class="caption">크론 줄은 있으나 꺼져 있다 — 고장이 아니다.</p>${table(off, now)}`;

  $('ops-caption').textContent = `${rows.length}개 단계`;
  $('ops-fetched').textContent = `마지막 갱신 ${now.toTimeString().slice(0, 5)}`;
}

async function load() {
  $('error').textContent = '';
  try {
    const rows = await apiAll('/pipeline_health', OPS_QUERY);
    render(rows, new Date());
  } catch (err) {
    $('error').textContent = err.message;
    $('ops-caption').textContent = '불러오지 못했습니다';
  }
}

$('ops-refresh').addEventListener('click', load);
load();
