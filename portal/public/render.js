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
