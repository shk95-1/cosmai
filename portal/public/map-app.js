// 구조 지도의 배선. 판단은 전부 map.js/query.js 의 순수 함수이고 여기서는 그것을 DOM 과 fetch 에
// 엮는다 — app.js·ops-app.js 와 같은 분리라서 이 파일에는 테스트가 없다.
//
// 라이브러리를 들이지 않는다. render.js 가 이미 SVG 를 템플릿 문자열로 손으로 그리고(막대·축·산점도),
// 노드 28개는 열 배치로 충분하다. 포털의 의존성 0 을 지키는 값이 자동배치보다 크다.
import { buildQuery, PAGE_SIZE, nextPageOffset, describeError, MAP_QUERIES } from './query.js';
import { nodesOf, feedbackEdges, positions, canvasSize, labelOf } from './map.js';

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

// 팔마다 색이 다르면 어느 수집기의 흐름인지 눈으로 따라갈 수 있다. 저장소는 팔이 없으므로
// 중립색이다 — 여러 팔이 같은 표를 먹일 수 있어 하나의 팔 색을 주면 거짓이 된다.
const ARM_CLASS = {
  commerce: 'node-commerce',
  youtube: 'node-youtube',
  naver: 'node-naver',
  analyze: 'node-analyze',
};

function nodeClass(node, stageByKey) {
  if (node.kind === 'store') return 'node-store';
  const stage = stageByKey.get(node.key);
  if (stage && stage.enabled === false) return 'node-off'; // 선언상 꺼진 단계는 회색이다(#39)
  return ARM_CLASS[stage ? stage.arm : ''] || 'node-store';
}

// 엣지는 열에서 열로 가므로 오른쪽 변에서 나와 왼쪽 변으로 든다. 되먹임은 반대 방향이라
// 위로 크게 돌려 노드를 가로지르지 않게 한다.
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

function render(edges, stages, now) {
  const stageByKey = new Map(stages.map((s) => [s.stage_key, s]));
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
    // 라벨이 상자보다 길면 잘린다. 전체 이름은 title 로 남긴다.
    return `<g><title>${esc(node.key)}</title>
      <rect class="map-node ${nodeClass(node, stageByKey)}" x="${box.x}" y="${box.y}" width="${box.w}" height="${box.h}" rx="6"/>
      <text class="map-label" x="${box.x + 10}" y="${box.y + box.h / 2 + 4}">${esc(label)}</text>
    </g>`;
  }).join('');

  $('map-canvas').innerHTML =
    `<svg class="viz-root" viewBox="0 0 ${width} ${height}" role="img" aria-label="파이프라인 구조도">${lines}${boxes}</svg>`;

  const feedbackCount = back.size;
  $('map-legend').innerHTML =
    `<p class="caption">실선 오른쪽 방향 = 단계가 표를 <b>쓴다</b> · 실선 왼쪽에서 = 표를 <b>읽는다</b> · `
    + `점선 = <b>되먹임</b>(같은 단계가 제 산출을 다시 읽는다, ${feedbackCount}개). `
    + `회색 상자 = 저장소, 색 상자 = 단계(팔별), 흐린 상자 = 선언상 꺼진 단계.</p>`;

  $('map-caption').textContent = `노드 ${nodesOf(edges).length}개 · 엣지 ${edges.length}개`;
  $('map-fetched').textContent = `마지막 갱신 ${now.toTimeString().slice(0, 5)}`;
}

async function load() {
  $('error').textContent = '';
  try {
    const [edges, stages] = await Promise.all([
      apiAll('/pipeline_edge', MAP_QUERIES.edge),
      apiAll('/pipeline_stage', MAP_QUERIES.stage),
    ]);
    if (edges.length === 0) {
      $('map-canvas').innerHTML = '<p class="empty-note">선언된 엣지가 없음 — 시드가 아직 안 돌았습니다.</p>';
      $('map-caption').textContent = '엣지 0개';
      return;
    }
    render(edges, stages, new Date());
  } catch (err) {
    $('error').textContent = err.message;
    $('map-caption').textContent = '불러오지 못했습니다';
  }
}

$('map-refresh').addEventListener('click', load);
load();
