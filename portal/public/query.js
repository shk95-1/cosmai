// PostgREST 요청 조립과 응답 파싱의 순수 함수들. DOM·fetch 를 만지지 않아서
// node --test 로 그대로 검증된다 (data-portal/public/query.js 의 같은 이유).
// 화면 배선은 app.js 의 몫이다.

// PGRST_DB_MAX_ROWS 와 같은 값 — 한 응답의 상한. CSV 내려받기가 이 크기로
// offset 을 옮기며 이어 읽는다.
export const PAGE_SIZE = 1000;

// PostgREST 문법의 쿼리스트링을 만든다. 앞의 '?' 는 붙이지 않는다.
// order 를 항상 붙이는 것이 핵심: 정렬 없이 offset 을 옮기면 페이지 사이에
// 행이 중복되거나 빠진다 (DB 가 순서를 보장하지 않는다).
export function buildQuery({ select, filters, order, limit, offset } = {}) {
  const params = new URLSearchParams();
  if (select && select.length > 0) params.append('select', select.join(','));
  for (const f of filters || []) {
    if (!f || !f.column || f.value === undefined || f.value === null || f.value === '') continue;
    params.append(f.column, `${f.op}.${f.value}`);
  }
  if (order) params.append('order', order);
  if (limit !== undefined) params.append('limit', String(limit));
  if (offset !== undefined) params.append('offset', String(offset));
  return params.toString();
}

// 'Content-Range: 0-999/65646' 의 슬래시 뒤가 전체 개수다. '*' 이면 서버가
// 세지 않은 것(Prefer: count=exact 가 빠졌다는 뜻)이라 개수를 모른다(null).
export function parseContentRange(header) {
  if (!header) return null;
  const total = String(header).split('/')[1];
  if (total === undefined || total === '*') return null;
  const n = Number(total);
  return Number.isFinite(n) ? n : null;
}

// 그 응답이 실제로 담은 행 수. CSV 를 줄 수로 세면 개행 포함 값에서 어긋나므로
// 서버가 세어 보낸 이 숫자만 믿는다.
export function rangeLength(header) {
  if (!header) return 0;
  const range = String(header).split('/')[0];
  if (range === '*' || !range.includes('-')) return 0;
  const [start, end] = range.split('-').map(Number);
  if (!Number.isFinite(start) || !Number.isFinite(end)) return 0;
  return end - start + 1;
}

// 다음 페이지의 offset, 더 받을 것이 없으면 null. 서버가 이번 페이지에서
// 실제로 보낸 행 수(rangeLength)와 전체 개수(parseContentRange)로 판단한다 —
// '*'(개수 모름)일 때도 이번 페이지가 PAGE_SIZE 보다 짧으면 마지막 페이지다.
export function nextPageOffset(offset, header) {
  const got = rangeLength(header);
  if (got === 0) return null;
  const total = parseContentRange(header);
  const next = offset + got;
  if (total !== null && next >= total) return null;
  if (got < PAGE_SIZE) return null;
  return next;
}

// CSV 페이지를 이어붙인다. 두 번째 페이지부터는 헤더 줄을 버린다.
export function appendCsvPage(accumulated, page, isFirst) {
  if (isFirst) return page;
  const newline = page.indexOf('\n');
  if (newline === -1) return accumulated;
  const body = page.slice(newline + 1);
  if (body === '') return accumulated;
  const sep = accumulated.endsWith('\n') || accumulated === '' ? '' : '\n';
  return accumulated + sep + body;
}

// 'analysis_run' 은 anon 화이트리스트에 없다(#11 1차 결정) — metrics_need 가
// 이미 담고 온 run_id 중 최댓값을 "최신 run"으로 쓴다.
export function latestRunId(rows) {
  let max = null;
  for (const r of rows || []) {
    const id = Number(r && r.run_id);
    if (Number.isFinite(id) && (max === null || id > max)) max = id;
  }
  return max;
}

// need_key/product_ref 등 정렬 가능한 표 정렬. 원본 배열은 건드리지 않는다.
export function sortRows(rows, key, dir = 'desc') {
  const sign = dir === 'asc' ? 1 : -1;
  return [...(rows || [])].sort((a, b) => {
    const av = a[key];
    const bv = b[key];
    if (av === bv) return 0;
    if (av === null || av === undefined) return 1;
    if (bv === null || bv === undefined) return -1;
    return av > bv ? sign : -sign;
  });
}

// mentions 를 dimKey(format|attribute|brand) 값별로 합산해 상위 n개를 낸다.
// 빈 문자열(marginal 아님을 나타내는 다른 축이 채워진 행)은 그 축의 "값 없음"이라
// 제외한다 — 계약 판정 #7(교차표 marginal 과 PK 충돌)의 이중 계산을 피하려면
// 정식 marginal 질의가 필요하지만, 1차 화면은 근사 상위 목록으로 충분하다.
export function topByDimension(rows, dimKey, n = 5) {
  const sums = new Map();
  for (const r of rows || []) {
    const v = r[dimKey];
    if (!v) continue;
    sums.set(v, (sums.get(v) || 0) + Number(r.mentions || 0));
  }
  return [...sums.entries()]
    .map(([value, mentions]) => ({ value, mentions }))
    .sort((a, b) => b.mentions - a.mentions)
    .slice(0, n);
}

// 화면이 내려받는 CSV 파일의 이름 — 화면(screen)·scope·시각으로 구분한다.
export function buildFileName(screen, scope, ext, now) {
  const p = (x) => String(x).padStart(2, '0');
  const stamp =
    `${now.getUTCFullYear()}${p(now.getUTCMonth() + 1)}${p(now.getUTCDate())}` +
    `-${p(now.getUTCHours())}${p(now.getUTCMinutes())}`;
  const scopePart = scope ? `.${scope}` : '';
  return `needs.${screen}${scopePart}.${stamp}.${ext}`;
}

const BOM = '﻿';

// 저장할 CSV 본문에 UTF-8 BOM 을 앞에 둔다 — Excel 이 시스템 코드페이지(CP949)로
// 읽어 한글을 깨뜨리는 것을 막는 유일한 신호다(BOM 없는 파일은 CP949로 읽힌다).
export function fileBody(text) {
  return text.startsWith(BOM) ? text : BOM + text;
}

// 값 하나를 CSV 필드로 이스케이프한다. 콤마·따옴표·개행이 있으면 따옴표로 감싼다.
function csvField(v) {
  if (v === null || v === undefined) return '';
  const s = String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

// 행 배열을 CSV 텍스트로 만든다 — columns 의 순서가 곧 헤더 순서다.
export function rowsToCsv(rows, columns) {
  const header = columns.map(csvField).join(',');
  const body = (rows || []).map((r) => columns.map((c) => csvField(r[c])).join(','));
  return [header, ...body].join('\n');
}

// 자주 만나는 PostgREST 오류에 한 줄 안내를 덧붙인다. 원문은 지우지 않는다 —
// 안내는 도움일 뿐, 진단의 근거는 서버가 보낸 말이다.
const ERROR_HINTS = {
  '42501': '권한이 없는 테이블입니다 (익명에 노출되지 않았습니다).',
  '42703': '없는 컬럼입니다.',
  PGRST205: '없는 테이블입니다 — 스키마 선택을 확인하세요.',
};

export function describeError(body) {
  const code = body && body.code ? String(body.code) : '';
  const message = body && body.message ? String(body.message) : '알 수 없는 오류';
  const head = code ? `${code} — ${message}` : message;
  const hint = ERROR_HINTS[code];
  return hint ? `${head}\n${hint}` : head;
}
