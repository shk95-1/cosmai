// 관제 페이지의 배선. 판단이 필요한 계산은 전부 ops.js/query.js 의 순수 함수이고 여기서는
// 그것을 DOM 과 fetch 에 엮는다 — app.js 와 같은 분리라서 이 파일에는 테스트가 없다.
//
// 지표 페이지(index.html)와 부팅을 나눈 이유: 관제만 보려고 열어도 metrics_need 세 벌을 받는
// 일이 없어야 한다. 이 페이지가 받는 것은 pipeline_health 한 표뿐이다(#139).
import { buildQuery, PAGE_SIZE, nextPageOffset, describeError, OPS_QUERY } from './query.js';
import { problems, problemCount, byArm, disabled, relativeTime, describeInterval, severityOf } from './ops.js';

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
      try { body = await res.json(); } catch { /* JSON 아닌 오류 본문 */ }
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

// 심각도 하나가 색을 고르고, 배지 둘이 그 심각도가 어디서 왔는지 말한다.
const SEVERITY_CLASS = ['sev-critical', 'sev-warn', 'sev-idle', 'sev-ok', 'sev-muted'];
const severityClass = (row) => SEVERITY_CLASS[severityOf(row)] || 'sev-idle';

function statsOf(row) {
  if (row.requests === null || row.requests === undefined) return '—';
  const parts = [`${row.requests} req`, `${row.ok ?? 0} ok`];
  if (row.blocked) parts.push(`${row.blocked} blocked`);
  if (row.failed) parts.push(`${row.failed} failed`);
  if (row.p90_ms) parts.push(`p90 ${row.p90_ms}ms`);
  return parts.join(' · ');
}

function rowHtml(row, now) {
  // 절대시각은 title 로 남긴다 — 본문이 상대시각이라야 늦었는지를 사람이 계산하지 않는다.
  // 기준 TZ 는 #89 가 정하고, 그때 이 자리도 함께 고친다.
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

  // 문제가 없으면 절이 통째로 사라진다.
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
