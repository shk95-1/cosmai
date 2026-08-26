// 화면별로 "어떤 run의 어떤 행"을 보여줄지 고르는 순수 함수. app.js 가 DOM에
// 꽂기 전에 이 판단을 분리해 둔 것은 이 판단 자체가 틀리기 쉬워서다.
import { okRunsByRecency, sortRows } from './query.js';

// need·wish 는 같은 run_id를 공유하지 않는다(에픽 #16 §1단계 판정 4, 시드는
// slice-suncare/p1/p9 별로 run이 따로다) — 표마다 자기 run을 따로 구한다.
// 하나의 runId로 두 표를 거르면 두 run이 어긋나는 순간 한쪽이 통째로 빈다
// (수정 라운드 1: 시드에서 need=2, wish=3이라 이 버그가 화면 2를 항상 비웠다).
//
// #87: runs(analysis_run)를 finished_at 최근순으로 훑어, 그 run_id 가 실제로 그
// 표(need/wish)에 행을 남긴 첫 run을 고른다 — run_id 최댓값이 아니라 "언제 끝났는가"로
// 고르므로, 손으로 돌린 aggregate 가 재사용한 더 작은 run_id도 최신으로 잡힌다.
export function latestRuns(runs, need, wish) {
  const ordered = okRunsByRecency(runs);
  const needIds = new Set((need || []).map((r) => r.run_id));
  const wishIds = new Set((wish || []).map((r) => r.run_id));
  const needRun = ordered.find((r) => needIds.has(r.run_id)) || null;
  const wishRun = ordered.find((r) => wishIds.has(r.run_id)) || null;
  return {
    needRunId: needRun ? needRun.run_id : null,
    wishRunId: wishRun ? wishRun.run_id : null,
    needRun,
    wishRun,
  };
}

export function scopesForRun(rows, runId) {
  return [...new Set((rows || []).filter((r) => r.run_id === runId).map((r) => r.scope))].sort();
}

export function needRowsForScope(need, runId, scope) {
  return (need || []).filter((r) => r.run_id === runId && r.product_ref === '' && r.month === '' && r.scope === scope);
}

export function wishRowsForScope(wish, runId, scope) {
  return (wish || []).filter((r) => r.run_id === runId && r.scope === scope);
}

// 제품 축 행은 scope 마다 한 벌씩 나온다(카테고리별 + 롤업 'all', #41). 같은 제품이
// 자기 카테고리와 'all'에서 두 번 걸리면 상위 20이 중복으로 채워지므로, 롤업이 있으면
// 롤업만 본다 — 'all'은 동의어를 접은 뒤의 전 제품을 한 번씩 담는다. --scope 로 좁혀 돈
// run 에는 'all'이 없어서, 그때는 있는 scope 를 그대로 쓴다.
export function productRows(need, runId, limit = 20) {
  // month 는 카테고리 합·제품 축 모두 ''다. 월 축이 생기면 그 행은 여기 섞이면 안 된다.
  const rows = (need || []).filter((r) => r.run_id === runId && r.product_ref !== '' && r.month === '');
  const rolled = rows.filter((r) => r.scope === 'all');
  return sortRows(rolled.length ? rolled : rows, 'unresolved', 'desc').slice(0, limit);
}

// finished_at 은 '2026-08-26T05:01:31.074893+00:00' 같은 ISO 문자열이다. Date 로 파싱해
// 지역시간으로 찍으면 같은 run 이 보는 기계마다 다른 시각으로 적히므로 문자열에서 자른다.
function stampOf(finishedAt) {
  const m = /^\d{4}-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/.exec(String(finishedAt || ''));
  return m ? `${m[1]}-${m[2]} ${m[3]}:${m[4]}` : '';
}

// versions 는 lexicon 처럼 중첩된 값까지 담아 헤더 네 줄을 먹었다(#122) — 요약에는
// 파이프라인을 특정하는 한 짝만 적고 나머지는 접히는 상세의 몫이다. extractor 를 먼저
// 보는 것은 화면의 숫자가 무엇으로 뽑혔는지가 가장 먼저 궁금한 값이어서다.
const HEADLINE_VERSION = 'extractor';
function headlineVersion(versions) {
  if (!versions || typeof versions !== 'object') return '';
  const keys = Object.keys(versions);
  const key = keys.includes(HEADLINE_VERSION)
    ? HEADLINE_VERSION
    : keys.find((k) => typeof versions[k] === 'string');
  return key ? `${key} ${versions[key]}` : '';
}

// run 하나를 "#24 · 08-26 05:01 · extractor rule-v2.3" 한 줄로 — 손 재집계 직후 화면이
// 그 run 을 골랐는지 눈으로 확인하려면 run_id 만으로는 부족하다(#87 완료 기준).
export function runBrief(run) {
  if (!run) return '없음';
  return [`#${run.run_id}`, stampOf(run.finished_at), headlineVersion(run.versions)]
    .filter(Boolean).join(' · ');
}

function runDetail(name, run) {
  const versions = run.versions && typeof run.versions === 'object' ? JSON.stringify(run.versions) : '';
  return [`${name} run #${run.run_id}`, run.note, versions].filter(Boolean).join('\n');
}

// 헤더에 걸 한 줄(summary)과 <details> 안에 접을 전문(detail)을 나눈다 — app.js 가
// 그 둘을 DOM 으로 엮는다. 나누는 판단 자체는 순수 함수라야 테스트가 붙는다.
export function runCaptionParts(needRun, wishRun) {
  if (!needRun && !wishRun) return { summary: '데이터 없음', detail: '' };
  // 실제로는 한 analyze run 이 두 표를 다 쓴다(run #24) — 같은 것을 두 번 적으면
  // 애초에 접으려던 이유(길이)가 그대로 남는다.
  const same = needRun && wishRun && needRun.run_id === wishRun.run_id;
  const pairs = same
    ? [['need·wish', needRun]]
    : [['need', needRun], ['wish', wishRun]];
  const summary = same
    ? `need·wish run ${runBrief(needRun)}`
    : `need run ${runBrief(needRun)} · wish run ${runBrief(wishRun)}`;
  const detail = pairs.filter(([, r]) => r).map(([name, r]) => runDetail(name, r)).join('\n\n');
  return { summary, detail };
}

// 셀렉트의 첫 항목은 사전순 첫 scope 라 "01 > 마스크팩 > 시트팩" 같은 우연한 카테고리가
// 첫 화면이 됐다(#122). 롤업 'all' 이 있으면 그것이 전체 그림이고, 없으면 행이 가장 많은
// scope 가 그 run 이 실제로 본 곳이다. 동률은 사전순으로 끊는다 — 새로고침마다 첫 화면이
// 바뀌면 무엇을 보고 있는지 알 수 없다.
export function defaultScope(rows, runId) {
  const counts = new Map();
  for (const r of rows || []) {
    if (!r || r.run_id !== runId) continue;
    counts.set(r.scope, (counts.get(r.scope) || 0) + 1);
  }
  if (counts.size === 0) return null;
  if (counts.has('all')) return 'all';
  let best = null;
  for (const scope of [...counts.keys()].sort()) {
    if (best === null || counts.get(scope) > counts.get(best)) best = scope;
  }
  return best;
}

// 0 나누기 방어: 분모가 없거나 0 이면 비율은 null 이다 — 0 으로 눕히면 "분모가 없는
// 니즈"와 "정말 0 인 니즈"가 같은 그림이 되어 산점도가 거짓말을 한다.
export function safeRatio(numerator, denominator) {
  const n = Number(numerator);
  const d = Number(denominator);
  if (numerator === null || numerator === undefined || !Number.isFinite(n)) return null;
  if (denominator === null || denominator === undefined || !Number.isFinite(d) || d === 0) return null;
  return n / d;
}

// 화면 4가 쓰는 행: 카테고리 합 행에 세 비율을 얹는다. 지속(월)·확산(제품)은 산점도의
// x·y 다. new_ratio 는 unresolved_new(신제품만의 미해결비, 002_audit_additive A4)를
// 전체 미해결비로 나눈 값이라 1(=100%)이 "신제품도 같은 수준"이고 1 을 넘을 수 있다 —
// 0~1 이 아니므로 산점도 축으로는 쓰지 않는다. 원본 컬럼은 그대로 둔다.
export function needCharacterRows(need, runId, scope) {
  return needRowsForScope(need, runId, scope).map((r) => ({
    ...r,
    persist_month_ratio: safeRatio(r.persist_months, r.persist_months_total),
    persist_product_ratio: safeRatio(r.persist_products, r.persist_products_total),
    new_ratio: safeRatio(r.unresolved_new, r.unresolved),
  }));
}

// yt_neg/yt_pos 가 전부 0 인 scope 는 유튜브 수집분이 없다는 뜻 — 빈 차트 대신
// 문구를 보여야 "언급 0 건"과 "그 출처를 안 모았다"가 구분된다.
export function hasYoutubeMentions(rows) {
  return (rows || []).some((r) => (Number(r.yt_neg) || 0) > 0 || (Number(r.yt_pos) || 0) > 0);
}

// 값이 null 인 행(분모 0)은 막대 차트에서 뺀다 — Number(null)||0 이 0% 막대로 둔갑해
// "신규가 하나도 없다"는 없는 사실을 그린다.
export function rowsWithValue(rows, key) {
  return (rows || []).filter((r) => {
    const v = r[key];
    return v !== null && v !== undefined && v !== '' && Number.isFinite(Number(v));
  });
}

// ---- 화면 3: 제품 이름 -----------------------------------------------------

// metrics_need 는 ref 만 갖고 있어 화면 3 의 막대 라벨이 'oy:A000000149577' 이었다.
// needs.product_ref 에 brand·name 이 있고 anon 화이트리스트에도 들어 있다(#11 입력).
// name 은 '[8월올영픽] … 80ml 1+1 기획' 처럼 기획 문구와 용량을 달고 있어 라벨에는
// 그것을 걷어낸 name_norm 을 쓰고, 없을 때만 name 으로 내려간다.
export function productNameIndex(rows) {
  const index = new Map();
  for (const r of rows || []) {
    if (!r || !r.product_ref) continue;
    index.set(r.product_ref, { brand: r.brand || '', name: r.name_norm || r.name || '' });
  }
  return index;
}

// 카탈로그에 없는 ref 는 ref 그대로 둔다. 링커가 못 붙인 mention 은 사이트의 원래 키가
// 그대로 ref 가 되는데(aggregate 의 _product), 그 자리에 이름을 지어 주면 화면이
// 파이프라인이 하지 않은 연결을 주장한다.
export function productLabel(ref, index) {
  const hit = index && typeof index.get === 'function' ? index.get(ref) : undefined;
  if (!hit) return String(ref);
  const parts = [hit.brand, hit.name].filter(Boolean);
  return parts.length ? parts.join(' · ') : String(ref);
}

// 막대의 라벨 자리는 폭이 정해져 있어 긴 이름은 옆 막대 위로 넘친다 — 자른 라벨을 그리고
// 전체 이름은 <title>(호버)이 갖는다.
export function truncateLabel(text, max = 20) {
  const s = String(text);
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

// 화면 3 의 행에 라벨 두 벌(전체·자른 것)을 얹는다. 원래 컬럼은 그대로 남는다 —
// product_ref 는 표에도 남아야 링커가 못 붙인 자리를 알아볼 수 있다.
export function withProductNames(rows, index, max = 20) {
  return (rows || []).map((r) => {
    const product = productLabel(r.product_ref, index);
    return { ...r, product, product_short: truncateLabel(product, max) };
  });
}

// ---- 화면 5: 기간(월) 축 (#130) ---------------------------------------------

// 월 행은 카테고리 합에만 붙는다(#129: month <> '' 이고 product_ref = ''). 질의가 이미
// 그렇게 좁혀 오지만 여기서 다시 거르는 것은, 이 함수가 받는 배열이 늘 그 질의의 응답이라는
// 보장이 없어서다 — 전체 기간 행이 한 줄만 새도 그 값이 한 달의 값으로 읽힌다.
function monthRowsOf(need, runId) {
  return (need || []).filter((r) => r && r.run_id === runId && r.month !== '' && r.month !== undefined
    && r.month !== null && r.product_ref === '');
}

// 판에 세울 달의 상한. 90 개월(2013-08~2026-08 실측)을 다 그리면 판이 2,500px 이 되고,
// 그 높이는 #122 가 화면 1 에서 걷어낸 바로 그것이다. 상한은 판의 성질이라 화면 쪽이 아니라
// 여기가 정본이고, index.html 의 캡션도 app.js 를 거쳐 이 값을 읽는다 — 두 벌로 두면
// 한쪽만 바뀌어 화면이 안 지키는 약속을 적는다.
export const MONTH_LIMIT = 24;

// 그 (run·scope·need_key) 의 월 행을 month 오름차순으로. month 는 'YYYY-MM' 문자열이라
// 사전순이 곧 시간순이다. limit 은 뒤에서 자른다(0 이면 전부) — 표는 0 으로 부른다:
// 판에서 밀린 달을 볼 자리가 화면에 하나는 있어야 한다.
export function monthRows(need, runId, scope, needKey, limit = MONTH_LIMIT) {
  const rows = monthRowsOf(need, runId)
    .filter((r) => r.scope === scope && r.need_key === needKey)
    .sort((a, b) => (a.month < b.month ? -1 : a.month > b.month ? 1 : 0));
  return limit && rows.length > limit ? rows.slice(rows.length - limit) : rows;
}

// 그 scope 에서 월 행을 가진 need_key 들. 셀렉트를 채우는 목록이자, 빈 배열이 곧
// "이 scope 에는 월 축이 없다"는 사실이다.
export function monthNeedKeys(need, runId, scope) {
  return [...new Set(monthRowsOf(need, runId).filter((r) => r.scope === scope).map((r) => r.need_key))].sort();
}

// "월 행이 없다"와 "그 달에 0 건"은 다른 사실이다 — 앞은 문구가 되고(집계가 아직 월 축을
// 내지 않았거나 그 scope 를 안 돌았다), 뒤는 폭 0 인 막대가 된다. hasYoutubeMentions 가
// 유튜브 축에서 하는 것과 같은 구별이다.
export function hasMonthRows(need, runId, scope) {
  return monthNeedKeys(need, runId, scope).length > 0;
}
