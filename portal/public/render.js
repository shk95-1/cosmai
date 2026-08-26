// 화면에 꽂을 SVG 문자열을 만드는 순수 함수들. DOM 을 만들지 않고 문자열을
// 돌려주므로 node --test 로 픽스처만으로 렌더 경로를 검증할 수 있다
// (완료 기준: 운영 PostgREST 없이 고정 JSON 픽스처로 렌더를 확인).
// 표 셀의 표시 포맷(formatCell)도 여기 있다 — 같은 값을 판과 표에서 다르게 적으면
// 같은 화면이 두 가지 자리수를 말한다.
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
const VALUE_W = 56; // 수치 라벨 여유
const AXIS_H = 18; // 0·최대 눈금 줄
const TEXT_MID = (ROW_H - GAP) / 2; // 막대 한가운데 — .viz-label/.viz-value 가 middle 로 눕는다

// 판 폭은 렌더 옵션이다. style.css 의 svg.viz-root{width:100%} 가 viewBox 를 판의
// 실제 폭까지 늘리므로, 폭이 고정이면 그 비율만큼 글자까지 확대된다(#122: 420 이
// 1382px 로 3.29배). 판이 놓이는 자리마다 폭이 다르니 폭도 자리가 정한다.
export const CHART_W_WIDE = 960; // 전체폭 판(화면 1 의 발산 막대, 화면 3)
export const CHART_W_SMALL = 420; // .chart-pair/.chart-triple 안의 판

// 값 라벨이 막대마다 붙으면 판이 표가 된다 — 상위 몇 개만 적고 나머지는 <title> 로만.
const VALUE_LABELS = 5;

// 행이 0 이면 테두리만 남은 빈 SVG 가 된다 — "언급 0 건"과 "그 축을 안 채웠다"를
// 구분하려면 문구가 필요하다(화면 4 의 유튜브 판이 쓰던 .empty-note 를 판마다 쓴다).
function emptyNote(text) {
  return `<p class="empty-note">${esc(text)}</p>`;
}

// 값이 큰 순으로 상위 n 개의 인덱스. 동률이 있어도 개수는 정확히 n 이다 — 문턱값으로
// 자르면 같은 값이 여럿일 때 라벨이 상한을 넘는다.
function topIndices(values, n = VALUE_LABELS) {
  return new Set(
    values.map((v, i) => [v, i]).sort((a, b) => b[0] - a[0]).slice(0, n).map(([, i]) => i),
  );
}

// 막대 판의 바닥 축: 0 과 최대 눈금, 그리고 절반 자리의 옅은 세로 그리드 하나.
// 눈금이 없으면 막대 길이가 무엇에 대한 비율인지 판 안에서 알 수 없다.
function barAxis(x0, x1, yBottom, maxText) {
  const mid = Math.round((x0 + x1) / 2);
  return `
    <line x1="${mid}" y1="0" x2="${mid}" y2="${yBottom}" class="viz-grid"/>
    <line x1="${x0}" y1="${yBottom}" x2="${x1}" y2="${yBottom}" class="viz-axis"/>
    <text x="${x0}" y="${yBottom + 12}" class="viz-tick">0</text>
    <text x="${x1}" y="${yBottom + 12}" class="viz-tick" text-anchor="end">${esc(maxText)}</text>
  `;
}

// neg 대 pos 를 need_key 별 한 행 대칭 막대로 — diverging blue(만족)/red(불만).
// 두 시리즈이므로 범례는 항상 그린다(색만으로 식별하지 않도록 텍스트 라벨도 붙인다).
// 두 줄 그룹 막대였던 것을 가운데 축 좌우로 편 것은 높이 때문이다(#122: 판 하나가
// 3,323px 였다) — 같은 정보가 절반 높이에 들어간다.
export function renderDivergingBars(rows, {
  negKey = 'neg', posKey = 'pos', labelKey = 'need_key',
  negLabel = '불만', posLabel = '만족',
  width = CHART_W_SMALL, empty = '',
} = {}) {
  const data = rows || [];
  if (data.length === 0 && empty) return emptyNote(empty);
  const max = Math.max(1, ...data.map((r) => Math.max(Number(r[negKey]) || 0, Number(r[posKey]) || 0)));
  // 양쪽에 수치 라벨이 나가므로 판 폭에서 두 번 뺀다.
  const plotL = LABEL_W + VALUE_W;
  const plotR = width - VALUE_W;
  const half = Math.max(1, Math.round((plotR - plotL) / 2));
  const cx = plotL + half;
  const yBottom = data.length * ROW_H;
  const height = yBottom + AXIS_H;
  const scale = (v) => Math.round((Math.abs(v) / max) * half);
  const labelled = topIndices(data.map((r) => Math.max(Number(r[negKey]) || 0, Number(r[posKey]) || 0)));

  const bars = data.map((r, i) => {
    const y0 = i * ROW_H;
    const name = esc(r[labelKey]);
    const neg = Number(r[negKey]) || 0;
    const pos = Number(r[posKey]) || 0;
    const negW = scale(neg);
    const posW = scale(pos);
    const show = labelled.has(i);
    return `
      <text x="0" y="${y0 + TEXT_MID}" class="viz-label">${name}</text>
      <g><title>${name} — ${esc(negLabel)} ${neg}</title><rect x="${cx - negW}" y="${y0}" width="${negW}" height="${ROW_H - GAP}" class="bar-neg" rx="4"/></g>
      <g><title>${name} — ${esc(posLabel)} ${pos}</title><rect x="${cx}" y="${y0}" width="${posW}" height="${ROW_H - GAP}" class="bar-pos" rx="4"/></g>
      ${show ? `<text x="${cx - negW - 6}" y="${y0 + TEXT_MID}" class="viz-value" text-anchor="end">${neg}</text>` : ''}
      ${show ? `<text x="${cx + posW + 6}" y="${y0 + TEXT_MID}" class="viz-value">${pos}</text>` : ''}
    `;
  }).join('');

  // 눈금은 왼쪽 최대 · 가운데 0 · 오른쪽 최대. 세로 그리드 몫은 가운데 0 축선이 겸한다.
  const axis = `
    <line x1="${cx}" y1="0" x2="${cx}" y2="${yBottom}" class="viz-axis"/>
    <text x="${cx - half}" y="${yBottom + 12}" class="viz-tick">${max}</text>
    <text x="${cx}" y="${yBottom + 12}" class="viz-tick" text-anchor="middle">0</text>
    <text x="${cx + half}" y="${yBottom + 12}" class="viz-tick" text-anchor="end">${max}</text>
  `;

  return `<svg class="viz-root" viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(labelKey)}별 ${esc(negLabel)}/${esc(posLabel)}">${axis}${bars}</svg>`;
}

// 단일 지표(unresolved 또는 population_share_pct) 를 need_key 별 순차(sequential)
// 막대로. hue 는 'blue'(기본, unresolved) 또는 'amber'(population_share_pct —
// 두 번째 순차 맥락은 다음 카테고리 슬롯 색을 쓴다는 팔레트 규칙).
export function renderMagnitudeBars(rows, {
  key, labelKey = 'need_key', hue = 'blue', fmt = (v) => v,
  width = CHART_W_SMALL, empty = '',
} = {}) {
  const data = rows || [];
  if (data.length === 0 && empty) return emptyNote(empty);
  const max = Math.max(1e-9, ...data.map((r) => Number(r[key]) || 0));
  const barMax = Math.max(1, width - LABEL_W - VALUE_W);
  const yBottom = data.length * ROW_H;
  const height = yBottom + AXIS_H;
  const scale = (v) => Math.round((Math.max(0, v) / max) * barMax);
  const cls = hue === 'amber' ? 'bar-magnitude-2' : 'bar-magnitude-1';
  const labelled = topIndices(data.map((r) => Number(r[key]) || 0));

  const bars = data.map((r, i) => {
    const y0 = i * ROW_H;
    const name = esc(r[labelKey]);
    const v = Number(r[key]) || 0;
    const w = scale(v);
    return `
      <text x="0" y="${y0 + TEXT_MID}" class="viz-label">${name}</text>
      <g><title>${name} — ${esc(fmt(v))}</title><rect x="${LABEL_W}" y="${y0}" width="${w}" height="${ROW_H - GAP}" class="${cls}" rx="4"/></g>
      ${labelled.has(i) ? `<text x="${LABEL_W + w + 6}" y="${y0 + TEXT_MID}" class="viz-value">${esc(fmt(v))}</text>` : ''}
    `;
  }).join('');

  const axis = barAxis(LABEL_W, LABEL_W + barMax, yBottom, fmt(max));
  return `<svg class="viz-root" viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(key)}">${axis}${bars}</svg>`;
}

// 화면 2: 상위 목록(topByDimension 결과)을 가로 막대로 — 단일 시리즈라 범례 없음.
export function renderTopBars(items, { width = CHART_W_SMALL, empty = '' } = {}) {
  const data = items || [];
  if (data.length === 0 && empty) return emptyNote(empty);
  const max = Math.max(1, ...data.map((r) => r.mentions));
  const barMax = Math.max(1, width - LABEL_W - VALUE_W);
  const yBottom = data.length * ROW_H;
  const height = yBottom + AXIS_H;
  const scale = (v) => Math.round((v / max) * barMax);
  const labelled = topIndices(data.map((r) => Number(r.mentions) || 0));

  const bars = data.map((r, i) => {
    const y0 = i * ROW_H;
    const name = esc(r.value);
    const w = scale(r.mentions);
    return `
      <text x="0" y="${y0 + TEXT_MID}" class="viz-label">${name}</text>
      <g><title>${name} — ${r.mentions}</title><rect x="${LABEL_W}" y="${y0}" width="${w}" height="${ROW_H - GAP}" class="bar-magnitude-1" rx="4"/></g>
      ${labelled.has(i) ? `<text x="${LABEL_W + w + 6}" y="${y0 + TEXT_MID}" class="viz-value">${r.mentions}</text>` : ''}
    `;
  }).join('');

  const axis = barAxis(LABEL_W, LABEL_W + barMax, yBottom, max);
  return `<svg class="viz-root" viewBox="0 0 ${width} ${height}" role="img" aria-label="상위 목록">${axis}${bars}</svg>`;
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
const LABEL_H = 13;   // 11px 글자 한 줄의 상자 높이
const LABEL_PAD = 2;  // 라벨 상자끼리 붙어 보이지 않게 두는 여유

// 0~1 비율만 좌표가 된다. null·빈 값·NaN(분모 0 을 screens.js 가 null 로 남긴 자리)은
// 좌표가 없다는 뜻이라 그 점을 그리지 않는다 — 0 으로 눕히면 없는 신호를 그린다.
function ratioOrNull(v) {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  if (!Number.isFinite(n)) return null;
  return Math.min(1, Math.max(0, n));
}

// 라벨 상자를 재려면 글자 폭이 필요한데 SVG 문자열만 만드는 순수 함수라 측정할 DOM 이
// 없다 — 한글은 글자당 약 11px, 그 밖은 약 6.2px 로 어림한다(11px 기준).
function textWidth(s) {
  let w = 0;
  for (const ch of String(s)) w += /[ᄀ-ᇿ　-〿㄰-㆏가-힯一-鿿＀-￯]/.test(ch) ? 11 : 6.2;
  return w;
}

function overlaps(a, b) {
  return !(a[2] + LABEL_PAD <= b[0] || b[2] + LABEL_PAD <= a[0] || a[3] + LABEL_PAD <= b[1] || b[3] + LABEL_PAD <= a[1]);
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

  // 상위 N 을 무조건 적으면 y=1 처럼 점이 몰린 자리에서 라벨이 서로 덮는다(#122).
  // 큰 점부터 자리를 잡고, 이미 놓인 라벨 상자와 겹치거나 판을 벗어나면 건너뛴다 —
  // 생략된 이름은 <title>(호버)로 남으므로 잃는 정보는 없다.
  const placed = [];
  const labels = new Map();
  for (const p of [...points].sort((a, b) => b.size - a.size)) {
    const r = radius(p.size);
    const cx = px(p.x);
    const cy = py(p.y);
    const w = textWidth(p.label);
    const right = [cx + r + 4, cy - LABEL_H / 2, cx + r + 4 + w, cy + LABEL_H / 2];
    const left = [cx - r - 4 - w, cy - LABEL_H / 2, cx - r - 4, cy + LABEL_H / 2];
    const fit = [right, left].find(
      (box) => box[0] >= 2 && box[2] <= PLOT_W - 2 && !placed.some((b) => overlaps(b, box)),
    );
    if (!fit) continue;
    placed.push(fit);
    labels.set(p, fit === right ? { x: right[0], anchor: 'start' } : { x: left[2], anchor: 'end' });
  }

  const dots = points.map((p) => {
    const r = Math.round(radius(p.size) * 10) / 10;
    const cx = Math.round(px(p.x) * 10) / 10;
    const cy = Math.round(py(p.y) * 10) / 10;
    const title = `${esc(p.label)} — ${esc(xLabel)} ${p.x.toFixed(2)}, ${esc(yLabel)} ${p.y.toFixed(2)}`;
    const at = labels.get(p);
    const text = at
      ? `<text x="${Math.round(at.x * 10) / 10}" y="${cy}" class="viz-point-label" text-anchor="${at.anchor}">${esc(p.label)}</text>`
      : '';
    return `<g><title>${title}</title><circle cx="${cx}" cy="${cy}" r="${r}" class="viz-dot"/></g>${text}`;
  }).join('');

  const axisLabels = `
    <text x="${PAD_L + innerW / 2}" y="${PLOT_H - 6}" class="viz-axis-label" text-anchor="middle">${esc(xLabel)}</text>
    <text x="14" y="${PAD_T + innerH / 2}" class="viz-axis-label" text-anchor="middle" transform="rotate(-90 14 ${PAD_T + innerH / 2})">${esc(yLabel)}</text>
  `;

  return `<svg class="viz-root" viewBox="0 0 ${PLOT_W} ${PLOT_H}" role="img" aria-label="${esc(xLabel)} 대 ${esc(yLabel)} 산점도">${axes}${ticks}${dots}${axisLabels}</svg>`;
}

// ---- 표 셀 포맷 ------------------------------------------------------------

// 컬럼이 무엇을 재는지는 이름이 안다 — app.js 가 textContent = r[c] 로 찍어
// 0.891304347826087 이 그대로 나오던 자리다(#122). 내려받는 CSV 는 원시값이 정본이라
// 이 포맷은 화면에만 쓴다.
const CELL_KIND = {
  neg: 'int', pos: 'int', mentions: 'int', yt_neg: 'int', yt_pos: 'int',
  persist_months: 'int', persist_months_total: 'int',
  persist_products: 'int', persist_products_total: 'int',
  denom_low: 'int', denom_site: 'int',
  unresolved: 'ratio', unresolved_new: 'ratio', new_ratio: 'ratio', low_share: 'ratio',
  persist_month_ratio: 'ratio', persist_product_ratio: 'ratio',
  population_share_pct: 'pct',
};

export function cellKind(column) {
  return CELL_KIND[column] || 'text';
}

export function formatCell(column, value) {
  if (value === null || value === undefined || value === '') return '';
  const kind = cellKind(column);
  // 이름을 모르는 컬럼이라도 float 은 새면 안 된다. 다만 product_ref 처럼 숫자로 보이는
  // 문자열 id 에 천단위를 넣으면 없는 자리수를 그리므로, 실제 숫자 타입만 손댄다.
  if (kind === 'text') {
    return typeof value === 'number' && !Number.isInteger(value) ? value.toFixed(2) : String(value);
  }
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  if (kind === 'int') return n.toLocaleString('en-US');
  if (kind === 'pct') return `${n.toFixed(2)}%`;
  return n.toFixed(2);
}

// 우측 정렬 대상인지 — 숫자는 자리수를 맞춰야 크기가 눈에 들어온다.
export function isNumericCell(column, value) {
  return cellKind(column) !== 'text' && value !== null && value !== undefined && value !== ''
    && Number.isFinite(Number(value));
}
