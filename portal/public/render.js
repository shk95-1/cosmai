// 화면에 꽂을 SVG 문자열을 만드는 순수 함수들. DOM 을 만들지 않고 문자열을
// 돌려주므로 node --test 로 픽스처만으로 렌더 경로를 검증할 수 있다
// (완료 기준: 운영 PostgREST 없이 고정 JSON 픽스처로 렌더를 확인).
// 색은 dataviz 스킬 팔레트의 역할(diverging blue/red, sequential blue→orange)을
// CSS 변수 클래스로만 참조한다 — 값은 style.css 한 곳에서 라이트/다크로 갈린다.

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

const ROW_H = 28;
const GAP = 2; // 인접 막대 사이 surface gap (marks-and-anatomy.md)
const LABEL_W = 96;
const CHART_W = 420;
const BAR_MAX = CHART_W - LABEL_W - 56; // 우측 수치 라벨 여유

// neg 대 pos 를 need_key 별 그룹 막대로 — diverging blue(만족)/red(불만).
// 두 시리즈이므로 범례는 항상 그린다(색만으로 식별하지 않도록 텍스트 라벨도 붙인다).
export function renderDivergingBars(rows, { negKey = 'neg', posKey = 'pos', labelKey = 'need_key' } = {}) {
  const data = rows || [];
  const max = Math.max(1, ...data.map((r) => Math.max(Number(r[negKey]) || 0, Number(r[posKey]) || 0)));
  const rowH = ROW_H * 2 + GAP;
  const height = data.length * rowH + 24;
  const scale = (v) => Math.round((Math.abs(v) / max) * BAR_MAX);

  const bars = data.map((r, i) => {
    const y0 = i * rowH;
    const neg = Number(r[negKey]) || 0;
    const pos = Number(r[posKey]) || 0;
    const negW = scale(neg);
    const posW = scale(pos);
    return `
      <text x="0" y="${y0 + ROW_H}" class="viz-label">${esc(r[labelKey])}</text>
      <rect x="${LABEL_W}" y="${y0}" width="${negW}" height="${ROW_H - GAP}" class="bar-neg" rx="4"/>
      <text x="${LABEL_W + negW + 6}" y="${y0 + ROW_H - 8}" class="viz-value">${neg}</text>
      <rect x="${LABEL_W}" y="${y0 + ROW_H}" width="${posW}" height="${ROW_H - GAP}" class="bar-pos" rx="4"/>
      <text x="${LABEL_W + posW + 6}" y="${y0 + ROW_H * 2 - 8}" class="viz-value">${pos}</text>
    `;
  }).join('');

  return `<svg class="viz-root" viewBox="0 0 ${CHART_W} ${height}" role="img" aria-label="need_key별 불만/만족">${bars}</svg>`;
}

// 단일 지표(unresolved 또는 population_share_pct) 를 need_key 별 순차(sequential)
// 막대로. hue 는 'blue'(기본, unresolved) 또는 'amber'(population_share_pct —
// 두 번째 순차 맥락은 다음 카테고리 슬롯 색을 쓴다는 팔레트 규칙).
export function renderMagnitudeBars(rows, { key, labelKey = 'need_key', hue = 'blue', fmt = (v) => v } = {}) {
  const data = rows || [];
  const max = Math.max(1e-9, ...data.map((r) => Number(r[key]) || 0));
  const height = data.length * ROW_H + 24;
  const scale = (v) => Math.round((Math.max(0, v) / max) * BAR_MAX);
  const cls = hue === 'amber' ? 'bar-magnitude-2' : 'bar-magnitude-1';

  const bars = data.map((r, i) => {
    const y0 = i * ROW_H;
    const v = Number(r[key]) || 0;
    const w = scale(v);
    return `
      <text x="0" y="${y0 + ROW_H - 8}" class="viz-label">${esc(r[labelKey])}</text>
      <rect x="${LABEL_W}" y="${y0}" width="${w}" height="${ROW_H - GAP}" class="${cls}" rx="4"/>
      <text x="${LABEL_W + w + 6}" y="${y0 + ROW_H - 8}" class="viz-value">${esc(fmt(v))}</text>
    `;
  }).join('');

  return `<svg class="viz-root" viewBox="0 0 ${CHART_W} ${height}" role="img" aria-label="${esc(key)}">${bars}</svg>`;
}

// 화면 2: 상위 목록(topByDimension 결과)을 가로 막대로 — 단일 시리즈라 범례 없음.
export function renderTopBars(items) {
  const data = items || [];
  const max = Math.max(1, ...data.map((r) => r.mentions));
  const height = data.length * ROW_H + 8;
  const scale = (v) => Math.round((v / max) * BAR_MAX);
  const bars = data.map((r, i) => {
    const y0 = i * ROW_H;
    const w = scale(r.mentions);
    return `
      <text x="0" y="${y0 + ROW_H - 8}" class="viz-label">${esc(r.value)}</text>
      <rect x="${LABEL_W}" y="${y0}" width="${w}" height="${ROW_H - GAP}" class="bar-magnitude-1" rx="4"/>
      <text x="${LABEL_W + w + 6}" y="${y0 + ROW_H - 8}" class="viz-value">${r.mentions}</text>
    `;
  }).join('');
  return `<svg class="viz-root" viewBox="0 0 ${CHART_W} ${height}" role="img" aria-label="상위 목록">${bars}</svg>`;
}

// 산점도 판. 막대들과 달리 두 축을 쓰므로 자기 좌표계를 갖는다 — 가로가 긴 판이면
// 오른쪽 위 사분면이 납작해져 "오래 가고 넓게 퍼짐"이 안 읽힌다.
const PLOT_W = 640;
const PLOT_H = 420;
const PAD_L = 52;
const PAD_R = 20;
const PAD_T = 16;
const PAD_B = 42;
const DOT_R_MIN = 4;  // 크기가 0 이어도 점은 보여야 한다
const DOT_R_MAX = 16;
const DOT_LABELS = 12; // 그 너머는 글자가 서로 덮어 못 읽는다 — 이름은 <title> 로만

// 0~1 비율만 좌표가 된다. null·빈 값·NaN(분모 0 을 screens.js 가 null 로 남긴 자리)은
// 좌표가 없다는 뜻이라 그 점을 그리지 않는다 — 0 으로 눕히면 없는 신호를 그린다.
function ratioOrNull(v) {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  if (!Number.isFinite(n)) return null;
  return Math.min(1, Math.max(0, n));
}

// 화면 4: 지속성(x) × 확산(y) 산점도. 점 크기는 sizeKey(미해결 규모)에 비례한다 —
// 오른쪽 위가 "오래 가고 넓게 퍼진" 니즈다. 원의 넓이가 값에 비례하도록 반지름은
// 제곱근으로 재는데, 반지름에 직접 비례시키면 큰 값이 실제보다 커 보인다.
export function renderScatter(rows, { xKey, yKey, sizeKey, labelKey = 'need_key', xLabel = '', yLabel = '' } = {}) {
  const points = (rows || [])
    .map((r) => ({
      x: ratioOrNull(r[xKey]),
      y: ratioOrNull(r[yKey]),
      size: Math.max(0, Number(r[sizeKey]) || 0),
      label: r[labelKey],
    }))
    .filter((p) => p.x !== null && p.y !== null);

  const innerW = PLOT_W - PAD_L - PAD_R;
  const innerH = PLOT_H - PAD_T - PAD_B;
  const px = (x) => PAD_L + x * innerW;
  const py = (y) => PAD_T + innerH - y * innerH;
  const maxSize = Math.max(1e-9, ...points.map((p) => p.size));
  const radius = (v) => DOT_R_MIN + (DOT_R_MAX - DOT_R_MIN) * Math.sqrt(v / maxSize);

  const axes = `
    <line x1="${PAD_L}" y1="${PAD_T}" x2="${PAD_L}" y2="${PAD_T + innerH}" class="viz-axis"/>
    <line x1="${PAD_L}" y1="${PAD_T + innerH}" x2="${PAD_L + innerW}" y2="${PAD_T + innerH}" class="viz-axis"/>
    <line x1="${px(0.5)}" y1="${PAD_T}" x2="${px(0.5)}" y2="${PAD_T + innerH}" class="viz-quadrant"/>
    <line x1="${PAD_L}" y1="${py(0.5)}" x2="${PAD_L + innerW}" y2="${py(0.5)}" class="viz-quadrant"/>
  `;

  const ticks = [0, 0.5, 1].map((t) => `
    <text x="${px(t)}" y="${PAD_T + innerH + 16}" class="viz-tick" text-anchor="middle">${t}</text>
    <text x="${PAD_L - 8}" y="${py(t)}" class="viz-tick" text-anchor="end">${t}</text>
  `).join('');

  const labelled = new Set(
    [...points].sort((a, b) => b.size - a.size).slice(0, DOT_LABELS),
  );

  const dots = points.map((p) => {
    const r = Math.round(radius(p.size) * 10) / 10;
    const cx = Math.round(px(p.x) * 10) / 10;
    const cy = Math.round(py(p.y) * 10) / 10;
    const title = `${esc(p.label)} — ${esc(xLabel)} ${p.x.toFixed(2)}, ${esc(yLabel)} ${p.y.toFixed(2)}`;
    const text = labelled.has(p)
      ? `<text x="${cx + r + 4}" y="${cy}" class="viz-point-label">${esc(p.label)}</text>`
      : '';
    return `<g><title>${title}</title><circle cx="${cx}" cy="${cy}" r="${r}" class="viz-dot"/></g>${text}`;
  }).join('');

  const axisLabels = `
    <text x="${PAD_L + innerW / 2}" y="${PLOT_H - 6}" class="viz-axis-label" text-anchor="middle">${esc(xLabel)}</text>
    <text x="14" y="${PAD_T + innerH / 2}" class="viz-axis-label" text-anchor="middle" transform="rotate(-90 14 ${PAD_T + innerH / 2})">${esc(yLabel)}</text>
  `;

  return `<svg class="viz-root" viewBox="0 0 ${PLOT_W} ${PLOT_H}" role="img" aria-label="${esc(xLabel)} 대 ${esc(yLabel)} 산점도">${axes}${ticks}${dots}${axisLabels}</svg>`;
}
