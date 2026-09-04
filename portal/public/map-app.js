// Wiring for the structure map. Judgement is entirely pure functions in map.js/query.js, and here it is only
// wired to DOM and fetch — the same split as app.js·ops-app.js, so this file has no tests.
//
// No library is brought in. render.js already draws SVG by hand as template strings (bars·axes·scatter),
// and 28 nodes are well served by column layout. Keeping the portal's zero dependencies is worth more than auto-layout.
import { buildQuery, PAGE_SIZE, nextPageOffset, describeError, MAP_QUERIES } from './query.js';
import { nodesOf, feedbackEdges, positions, canvasSize, labelOf } from './map.js';
import { severityClass } from './severity.js';

const API_BASE = `${window.location.protocol}//${window.location.hostname}:3000`;
const HEADERS = { 'Accept-Profile': 'needs', Prefer: 'count=exact' };
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

async function apiAll(path, { select, order }) {
  const rows = [];
  let offset = 0;
  for (;;) {
    const q = buildQuery({ select, order, limit: PAGE_SIZE, offset });
    let res;
    try {
      res = await fetch(`${API_BASE}${path}?${q}`, { headers: HEADERS });
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

// A different color per arm lets the eye follow which collector's flow it is. A store has no arm, so it is
// a neutral color — several arms can feed the same table, so giving it one arm's color would be false.
const ARM_CLASS = {
  commerce: 'node-commerce',
  youtube: 'node-youtube',
  naver: 'node-naver',
  analyze: 'node-analyze',
};

function nodeClass(node, stageByKey) {
  if (node.kind === 'store') return 'node-store';
  const stage = stageByKey.get(node.key);
  if (stage && stage.enabled === false) return 'node-off'; // a stage declared off is grey (#39)
  return ARM_CLASS[stage ? stage.arm : ''] || 'node-store';
}

// An edge goes column to column, so it leaves the right edge and enters the left. Feedback runs the opposite
// direction, so it is bowed up and over so it does not cut across a node.
function edgePath(from, to, isFeedback) {
  const x1 = isFeedback ? from.x : from.x + from.w;
  const y1 = from.y + from.h / 2;
  const x2 = isFeedback ? to.x + to.w : to.x;
  const y2 = to.y + to.h / 2;
  if (isFeedback) {
    const lift = Math.min(y1, y2) - 26;
    return `M ${x1} ${y1} C ${x1 - 40} ${lift}, ${x2 + 40} ${lift}, ${x2} ${y2}`;
  }
  const mid = (x1 + x2) / 2;
  return `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`;
}

function render(edges, stages, health, now) {
  const stageByKey = new Map(stages.map((s) => [s.stage_key, s]));
  const healthByKey = new Map((health || []).map((h) => [h.stage_key, h]));
  const pos = positions(edges);
  const { width, height } = canvasSize(edges);
  const back = new Set(feedbackEdges(edges).map((e) => `${e.from_key} ${e.to_key}`));

  const lines = edges.map((e) => {
    const from = pos.get(e.from_key);
    const to = pos.get(e.to_key);
    if (!from || !to) return '';
    const feedback = back.has(`${e.from_key} ${e.to_key}`);
    const cls = feedback ? 'edge edge-feedback' : e.from_kind === 'stage' ? 'edge edge-write' : 'edge edge-read';
    const title = `${e.from_key} → ${e.to_key}${e.note ? ` — ${e.note}` : ''}`;
    return `<g><title>${esc(title)}</title><path class="${cls}" d="${edgePath(from, to, feedback)}"/></g>`;
  }).join('');

  const boxes = nodesOf(edges).map((node) => {
    const box = pos.get(node.key);
    const label = labelOf(node);
    const row = healthByKey.get(node.key);
    // Status is laid on as a **left stripe**. The fill already states the arm (identity), and overlaying a
    // status color on top would give one shape two color systems, unreadable. The stripe is the same device
    // the ops table (#139) uses on its rows, so the two screens say the same thing.
    //
    // A store has no stripe. Since one table can be fed by several stages (trend_radar.product is fed by
    // both commerce:ranking and commerce:product), inheriting one stage's status would be false.
    const stripe = row
      ? `<rect class="map-stripe ${severityClass(row)}" x="${box.x}" y="${box.y}" width="4" height="${box.h}"/>`
      : '';
    const state = row ? ` — ${row.freshness} / ${row.last_run_status ?? '—'}` : '';
    return `<g><title>${esc(node.key + state)}</title>
      <rect class="map-node ${nodeClass(node, stageByKey)}" x="${box.x}" y="${box.y}" width="${box.w}" height="${box.h}" rx="6"/>
      ${stripe}
      <text class="map-label" x="${box.x + 12}" y="${box.y + box.h / 2 + 4}">${esc(label)}</text>
    </g>`;
  }).join('');

  $('map-canvas').innerHTML =
    `<svg class="viz-root" viewBox="0 0 ${width} ${height}" role="img" aria-label="파이프라인 구조도">${lines}${boxes}</svg>`;

  const feedbackCount = back.size;
  $('map-legend').innerHTML =
    `<p class="caption">실선 오른쪽 방향 = 단계가 표를 <b>쓴다</b> · 실선 왼쪽에서 = 표를 <b>읽는다</b> · `
    + `점선 = <b>되먹임</b>(같은 단계가 제 산출을 다시 읽는다, ${feedbackCount}개). `
    + `회색 상자 = 저장소, 색 상자 = 단계(팔별), 흐린 상자 = 선언상 꺼진 단계. `
    + `단계 왼쪽 띠 = 지금 상태(관제 화면과 같은 색). 저장소에 띠가 없는 것은 표 하나를 여러 `
    + `단계가 먹일 수 있어 한 단계의 상태를 물려주면 거짓이 되기 때문이다.</p>`;

  $('map-caption').textContent = `노드 ${nodesOf(edges).length}개 · 엣지 ${edges.length}개`;
  $('map-fetched').textContent = `마지막 갱신 ${now.toTimeString().slice(0, 5)}`;
}

async function load() {
  $('error').textContent = '';
  try {
    const [edges, stages, health] = await Promise.all([
      apiAll('/pipeline_edge', MAP_QUERIES.edge),
      apiAll('/pipeline_stage', MAP_QUERIES.stage),
      apiAll('/pipeline_health', MAP_QUERIES.health),
    ]);
    if (edges.length === 0) {
      $('map-canvas').innerHTML = '<p class="empty-note">선언된 엣지가 없음 — 시드가 아직 안 돌았습니다.</p>';
      $('map-caption').textContent = '엣지 0개';
      return;
    }
    render(edges, stages, health, new Date());
  } catch (err) {
    $('error').textContent = err.message;
    $('map-caption').textContent = '불러오지 못했습니다';
  }
}

$('map-refresh').addEventListener('click', load);
load();
