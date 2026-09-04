// Pure functions that build the SVG strings plugged into the screen. Since they build strings, not DOM,
// node --test can verify the render path with fixtures alone
// (the done criterion: confirm the render with fixed JSON fixtures, no live PostgREST).
// The table cell's display format (formatCell) lives here too — if the same value were written
// differently on the panel and the table, one screen would state two different digit places.
// Color references only the roles from the dataviz skill palette (diverging blue/red, sequential
// blue→orange) through CSS variable classes — the values split light/dark in the one place, style.css.

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

const ROW_H = 28;
const GAP = 2; // the surface gap between adjacent bars (marks-and-anatomy.md)
const LABEL_W = 96;
const VALUE_W = 56; // room for the numeric label
const AXIS_H = 18; // the 0·max tick row
const TEXT_MID = (ROW_H - GAP) / 2; // the middle of the bar — .viz-label/.viz-value sit at middle

// Panel width is a render option. Since style.css's svg.viz-root{width:100%} stretches the viewBox to
// the panel's actual width, a fixed width enlarges the text by that same ratio too (#122: 420 became
// 1382px, a 3.29x factor). Since width differs by where the panel sits, the spot decides the width too.
export const CHART_W_WIDE = 960; // the full-width panel (screen 1's diverging bars, screen 3)
export const CHART_W_SMALL = 420; // the panel inside .chart-pair/.chart-triple

// If a value label were attached to every bar the panel would become a table — only the top few are written, the rest live in <title> only.
const VALUE_LABELS = 5;

// Zero rows leaves an empty SVG with only a border — distinguishing "0 mentions" from "this axis was
// not filled" needs wording (every panel uses the .empty-note screen 4's YouTube panel used).
function emptyNote(text) {
  return `<p class="empty-note">${esc(text)}</p>`;
}

// The indices of the top n by value, descending. The count is exactly n even with ties — cutting
// by a threshold value would let the labels exceed the cap when several rows share a value.
function topIndices(values, n = VALUE_LABELS) {
  return new Set(
    values.map((v, i) => [v, i]).sort((a, b) => b[0] - a[0]).slice(0, n).map(([, i]) => i),
  );
}

// The bar panel's baseline axis: the 0 and max ticks, plus one faint vertical grid line at the midpoint.
// Without ticks there is no way within the panel to tell what the bar length is a ratio of.
function barAxis(x0, x1, yBottom, maxText) {
  const mid = Math.round((x0 + x1) / 2);
  return `
    <line x1="${mid}" y1="0" x2="${mid}" y2="${yBottom}" class="viz-grid"/>
    <line x1="${x0}" y1="${yBottom}" x2="${x1}" y2="${yBottom}" class="viz-axis"/>
    <text x="${x0}" y="${yBottom + 12}" class="viz-tick">0</text>
    <text x="${x1}" y="${yBottom + 12}" class="viz-tick" text-anchor="end">${esc(maxText)}</text>
  `;
}

// Draws neg vs. pos as one symmetric bar per need_key — diverging blue (satisfied)/red (dissatisfied).
// Since there are two series, the legend is always drawn (a text label is also attached so color alone does not have to carry identification).
// What used to be a two-row grouped bar was spread left/right of the center axis for height's sake (#122:
// one panel was 3,323px) — the same information now fits in half the height.
export function renderDivergingBars(rows, {
  negKey = 'neg', posKey = 'pos', labelKey = 'need_key',
  negLabel = '불만', posLabel = '만족',
  width = CHART_W_SMALL, empty = '',
} = {}) {
  const data = rows || [];
  if (data.length === 0 && empty) return emptyNote(empty);
  const max = Math.max(1, ...data.map((r) => Math.max(Number(r[negKey]) || 0, Number(r[posKey]) || 0)));
  // Since a numeric label goes out on both sides, it is subtracted from the panel width twice.
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

  // Ticks are max on the left · 0 in the middle · max on the right. The center 0 axis line doubles as the vertical grid.
  const axis = `
    <line x1="${cx}" y1="0" x2="${cx}" y2="${yBottom}" class="viz-axis"/>
    <text x="${cx - half}" y="${yBottom + 12}" class="viz-tick">${max}</text>
    <text x="${cx}" y="${yBottom + 12}" class="viz-tick" text-anchor="middle">0</text>
    <text x="${cx + half}" y="${yBottom + 12}" class="viz-tick" text-anchor="end">${max}</text>
  `;

  return `<svg class="viz-root" viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(labelKey)}별 ${esc(negLabel)}/${esc(posLabel)}">${axis}${bars}</svg>`;
}

// Draws a single metric (unresolved or population_share_pct) as a sequential
// bar per need_key. hue is 'blue' (default, unresolved) or 'amber' (population_share_pct —
// the palette rule that a second sequential context uses the next categorical slot's color).
// labelW·titleKey exist for screen 3 — 'brand · product name' overflows the 96px slot, and since the
// truncated label and the full name live in different columns, the column the tooltip reads must be set separately too.
export function renderMagnitudeBars(rows, {
  key, labelKey = 'need_key', titleKey = labelKey, hue = 'blue', fmt = (v) => v,
  width = CHART_W_SMALL, labelW = LABEL_W, empty = '',
} = {}) {
  const data = rows || [];
  if (data.length === 0 && empty) return emptyNote(empty);
  const max = Math.max(1e-9, ...data.map((r) => Number(r[key]) || 0));
  const barMax = Math.max(1, width - labelW - VALUE_W);
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
      <g><title>${esc(r[titleKey])} — ${esc(fmt(v))}</title><rect x="${labelW}" y="${y0}" width="${w}" height="${ROW_H - GAP}" class="${cls}" rx="4"/></g>
      ${labelled.has(i) ? `<text x="${labelW + w + 6}" y="${y0 + TEXT_MID}" class="viz-value">${esc(fmt(v))}</text>` : ''}
    `;
  }).join('');

  const axis = barAxis(labelW, labelW + barMax, yBottom, fmt(max));
  return `<svg class="viz-root" viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(key)}">${axis}${bars}</svg>`;
}

// Screen 2: draws the top list (topByDimension's result) as horizontal bars — a single series, so no legend.
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

// The scatter panel. Unlike the bars it uses two axes, so it has its own coordinate system — a wide panel
// would flatten the top-right quadrant, and "lasting long and spreading wide" would stop reading.
const PLOT_W = 640;
const PLOT_H = 420;
const PAD_L = 52;
const PAD_R = 20;
const PAD_T = 16;
const PAD_B = 42;
const DOT_R_MIN = 4;  // even a zero-size dot must still show
const DOT_R_MAX = 16;
const LABEL_H = 13;   // the box height of one line of 11px text
const LABEL_PAD = 2;  // margin kept so label boxes never look like they touch

// Only a 0-1 ratio becomes a coordinate. null·an empty value·NaN (the spot where screens.js leaves a
// zero denominator as null) means there is no coordinate, so that point is not drawn — flattening it to 0 would draw a signal that does not exist.
function ratioOrNull(v) {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  if (!Number.isFinite(n)) return null;
  return Math.min(1, Math.max(0, n));
}

// Measuring a label box needs the character width, but as a pure function that only builds an SVG
// string there is no DOM to measure with — Korean is estimated at about 11px per character, everything else at about 6.2px (11px baseline).
function textWidth(s) {
  let w = 0;
  for (const ch of String(s)) w += /[ᄀ-ᇿ　-〿㄰-㆏가-힯一-鿿＀-￯]/.test(ch) ? 11 : 6.2;
  return w;
}

function overlaps(a, b) {
  return !(a[2] + LABEL_PAD <= b[0] || b[2] + LABEL_PAD <= a[0] || a[3] + LABEL_PAD <= b[1] || b[3] + LABEL_PAD <= a[1]);
}

// Screen 4: persistence (x) × spread (y) scatter. Dot size is proportional to sizeKey (the unresolved scale) —
// the top right is a need that "lasts long and spreads wide." So the circle's area, not its radius, is
// proportional to the value, the radius is scaled by a square root — scaling the radius directly would make large values look bigger than they are.
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

  // Always writing the top N would overlap labels wherever points cluster, like at y=1 (#122).
  // Bigger dots are placed first, and any that would overlap an already-placed label box or run off the panel are skipped —
  // an omitted name still remains in <title> (on hover), so no information is lost.
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

// ---- Table cell format ------------------------------------------------------------

// The name knows what a column measures — this is the spot where app.js used to print
// 0.891304347826087 verbatim via textContent = r[c] (#122). The downloadable CSV is the source of truth
// on raw values, so this format is used on screen only.
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
  // Even a column with an unknown name must never leak a raw float. But a string id that merely looks
  // numeric, like product_ref, would draw digit places that do not exist if given thousands separators, so only actual numeric types are touched.
  if (kind === 'text') {
    return typeof value === 'number' && !Number.isInteger(value) ? value.toFixed(2) : String(value);
  }
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  if (kind === 'int') return n.toLocaleString('en-US');
  if (kind === 'pct') return `${n.toFixed(2)}%`;
  return n.toFixed(2);
}

// Whether this is a right-align target — a number's digit places must line up for its size to read at a glance.
export function isNumericCell(column, value) {
  return cellKind(column) !== 'text' && value !== null && value !== undefined && value !== ''
    && Number.isFinite(Number(value));
}
