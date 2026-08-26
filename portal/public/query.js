// PostgREST 요청 조립과 응답 파싱의 순수 함수들. DOM·fetch 를 만지지 않아서
// node --test 로 그대로 검증된다 (data-portal/public/query.js 의 같은 이유).
// 화면 배선은 app.js 의 몫이다.

// PGRST_DB_MAX_ROWS 와 같은 값 — 한 응답의 상한. CSV 내려받기가 이 크기로
// offset 을 옮기며 이어 읽는다.
export const PAGE_SIZE = 1000;

// PostgREST 문법의 쿼리스트링을 만든다. 앞의 '?' 는 붙이지 않는다.
// order 를 항상 붙이는 것이 핵심: 정렬 없이 offset 을 옮기면 페이지 사이에
// 행이 중복되거나 빠진다 (DB 가 순서를 보장하지 않는다).
//
// 빈 값의 필터는 기본적으로 버린다 — 아무것도 안 고른 셀렉트가 필터로 둔갑하지
// 않게 하려는 것이다. 다만 metrics_need 의 카테고리 합 행은 product_ref 가 실제로
// 빈 문자열이라 'product_ref=eq.' 가 유일한 필터다(#109) — 그 자리는 allowEmpty 로
// "빈 값을 값으로 쓰겠다"고 밝힌다.
export function buildQuery({ select, filters, order, limit, offset } = {}) {
  const params = new URLSearchParams();
  if (select && select.length > 0) params.append('select', select.join(','));
  for (const f of filters || []) {
    if (!f || !f.column || f.value === undefined || f.value === null) continue;
    if (f.value === '' && !f.allowEmpty) continue;
    params.append(f.column, `${f.op}.${f.value}`);
  }
  if (order) params.append('order', order);
  if (limit !== undefined) params.append('limit', String(limit));
  if (offset !== undefined) params.append('offset', String(offset));
  return params.toString();
}

// metrics_need 는 축이 셋이다 — 카테고리 합(화면 1·4) · 제품 축(화면 3) · 월 축(화면 5).
// 셋의 스펙이 app.js 의 지역 상수가 아니라 여기 있는 이유는 둘 다 "스펙 사이의 관계" 라서다.
//
// 하나는 배타성이다: 축 하나를 더하면 나머지 둘의 필터가 같이 좁혀져야 한다. 월 행이 얹힌
// 뒤 month=eq. 가 빠진 질의는 제 몫의 두 배를 받는다(#130, 실측 7,219행 → 대략 14,000).
// 다른 하나는 select 가 screens.js 의 소비 함수와 맺는 계약이다: PostgREST 는 select 에
// 적은 컬럼만 JSON 에 담으므로, 거르는 쪽이 보는 컬럼이 select 에 없으면 그 키는 응답에
// 아예 없고 비교는 언제나 거짓이 된다 — 화면이 통째로 빈다. 스펙이 순수 모듈에 있어야
// 테스트가 select 와 소비 함수를 한자리에서 맞춰 볼 수 있다(#130 수정 라운드).
//
// order 는 metrics_need 의 PK 전체(001_needs.sql) — run_id 만으로는 동률이 흔해 offset
// 페이징 중 행이 빠지거나 겹칠 수 있다(이 파일 머리말과 같은 이유).
const NEED_ORDER = 'run_id.desc,scope,need_key,month,product_ref';

// 운영 관제(#139)가 읽는 단 하나의 표 -- needs.pipeline_health. 판정(freshness·last_run_status)은
// 뷰가 이미 끝냈으므로 화면은 받아서 놓기만 한다.
//
// select 가 소비 함수가 거르는 컬럼을 빠짐없이 담아야 한다는 규칙은 여기서도 같다: PostgREST 는
// select 에 적은 컬럼만 JSON 에 담으므로, ops.js 의 isProblem 이 보는 freshness·last_run_status 나
// byArm 이 보는 arm 이 빠지면 그 비교가 언제나 거짓이 되고 화면이 통째로 빈다(#130 이 데인 자리).
//
// 필터도 정렬도 서버에 맡기지 않는다 -- 행이 선언된 단계 수(지금 14)뿐이라 한 페이지에 들어오고,
// 순서는 ops.js 의 순수 함수가 심각도로 정한다. 서버 정렬을 섞으면 그 판단이 두 자리로 갈린다.
export const OPS_QUERY = {
  select: [
    'stage_key', 'arm', 'dataset', 'enabled', 'expected_interval',
    'last_success_at', 'last_run_at', 'last_run_status', 'overdue_by', 'freshness',
    'requests', 'ok', 'blocked', 'failed', 'p90_ms',
  ],
  order: 'stage_key',
};

// 구조 지도(#142)가 읽는 둘. 엣지가 노드까지 진다(#141 -- 노드 표를 두지 않는 것이 설계다).
// 단계 표를 따로 받는 것은 arm 과 enabled 때문이다: 그림이 팔로 색을 나누고, 꺼진 단계를
// 회색으로 두려면 그 둘이 필요하다. 엣지만으로는 알 수 없다.
//
// 정렬을 서버에 맡긴다 -- 여기서는 페이지 경계를 안정시키는 것이 목적이고, 그림의 순서는
// map.js 의 순수 함수가 계층으로 정한다.
export const MAP_QUERIES = {
  edge: {
    select: ['from_key', 'from_kind', 'to_key', 'to_kind', 'note'],
    order: 'from_key,to_key',
  },
  stage: {
    select: ['stage_key', 'arm', 'dataset', 'enabled'],
    order: 'stage_key',
  },
};

export const NEED_QUERIES = {
  // 화면 1·4: 카테고리 합 행. product_ref·month 가 실제로 빈 문자열이라 두 eq.(allowEmpty)
  // 가 그것을 고르는 유일한 필터다(#109, #130).
  category: {
    select: [
      'run_id', 'scope', 'need_key', 'month', 'product_ref', 'neg', 'pos', 'unresolved',
      'population_share_pct',
      'yt_neg', 'yt_pos', 'persist_months', 'persist_months_total',
      'persist_products', 'persist_products_total', 'unresolved_new', 'low_share',
      'denom_low', 'denom_site',
    ],
    filters: [
      { column: 'product_ref', op: 'eq', value: '', allowEmpty: true },
      { column: 'month', op: 'eq', value: '', allowEmpty: true },
    ],
    order: NEED_ORDER,
  },
  // 화면 3: 제품 축 행만 — 합 행을 같이 받으면 상위 20 이 카테고리로 채워진다.
  product: {
    select: ['run_id', 'scope', 'need_key', 'month', 'product_ref', 'neg', 'pos', 'unresolved'],
    filters: [
      { column: 'product_ref', op: 'neq', value: '', allowEmpty: true },
      { column: 'month', op: 'eq', value: '', allowEmpty: true },
    ],
    order: NEED_ORDER,
  },
  // 화면 5: 월 행만 — 위의 둘과 정확히 겹치지 않는 반대편이다. 분모·persist_* 는 월 행에서
  // NULL 이라 받지 않는다(#129 의 결정: 그 달의 분모라는 것이 존재하지 않는다).
  // product_ref 는 값이 늘 빈 문자열이지만 반드시 받는다 — screens.js 의 monthRowsOf 가
  // 그것으로 거르는데, select 에 없으면 응답 행에 키가 없어 그 비교가 언제나 거짓이 되고
  // 화면 5 가 어떤 scope 에서도 "월 행이 없음" 만 낸다(#130 수정 라운드).
  month: {
    select: [
      'run_id', 'scope', 'need_key', 'month', 'product_ref',
      'neg', 'pos', 'unresolved', 'yt_neg', 'yt_pos',
    ],
    filters: [
      { column: 'month', op: 'neq', value: '', allowEmpty: true },
      { column: 'product_ref', op: 'eq', value: '', allowEmpty: true },
    ],
    // 이 질의에서 product_ref 는 늘 빈 값이라 앞의 넷이 곧 PK 다.
    order: 'run_id.desc,scope,need_key,month',
  },
};

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

// #87: 'analysis_run' 이 anon 화이트리스트에 들어온 뒤로 "최신 run" 판정은
// screens.js의 latestRuns/okRunsByRecency(analysis_run.finished_at·status)가 한다.
// 이 함수는 run_id 최댓값이 필요한 범용 자리(테스트 픽스처 등)에만 남는다.
export function latestRunId(rows) {
  let max = null;
  for (const r of rows || []) {
    const id = Number(r && r.run_id);
    if (Number.isFinite(id) && (max === null || id > max)) max = id;
  }
  return max;
}

// analysis_run 행 중 끝난 것(status='ok', finished_at 있음)만 최근순으로 정렬한다.
// 손으로 돌린 aggregate 는 note 로 기존 run 을 재사용해 더 작은 run_id 를 쓸 수 있어
// (analysis/aggregate/pipeline.py의 _run_id) run_id 크기로는 "최신"을 못 고른다(#87).
export function okRunsByRecency(runs) {
  return (runs || [])
    .filter((r) => r && r.status === 'ok' && r.finished_at)
    .slice()
    .sort((a, b) => new Date(b.finished_at) - new Date(a.finished_at));
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
