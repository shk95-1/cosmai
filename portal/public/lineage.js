// 계보 드릴다운의 판단 부분 — 순수 함수만. DOM 도 fetch 도 없어 portal/test 가 그대로 잰다
// (query/screens/render/ops 와 같은 분리, tool/checks/js).
//
// 두 뷰(needs.mention_lineage · needs.collection_lineage)가 이미 구간마다의 답을 갖고 있으므로
// 여기가 하는 일은 둘뿐이다: 지표 한 칸을 그 뷰의 필터로 옮기는 것, 그리고 그 칸이 지금 되짚을 수
// 있는 칸인지 판정하는 것.
//
// 판정을 화면에 두는 이유(#144): 그것은 `analysis_run` 만으로 계산되고 anon 이 그 표를 이미 읽는다.
// 지표 페이지가 부팅에서 받는 그 행들이 그대로 근거라, 뷰를 하나 더 만들어 같은 사실을 두 자리에서
// 주장하게 만들 필요가 없다.

// 집계가 모집단을 고르는 술어는 `extractor_version = ANY(...)` 하나이고, 그 목록이 그대로
// `analysis_run.versions.extractor` 에 ';' 로 이어져 있다(analysis/aggregate/pipeline.py 의 _versions).
//
// polarity 는 절대 같이 걸지 않는다: 한 extractor_version 이 polarity 두 판본을 담아, run 26 에 걸면
// neg 15,452 가 8,685 로 줄고 33행 중 2행만 맞는다(#144 판단 절 실측).
export function extractorsOf(run) {
  const raw = run && run.versions && typeof run.versions === 'object' ? run.versions.extractor : null;
  if (typeof raw !== 'string') return [];
  return raw.split(';').map((v) => v.trim()).filter(Boolean);
}

// 언급을 다시 쓰는 것은 `analyze` 뿐이다. eval 과 trend-quarter run 은 need_mention 을 건드리지
// 않으므로 그것까지 세면 되짚을 수 있는 칸도 "다시 쓴 실행이 있다" 로 닫힌다.
const REWRITER = 'analyze:';

function runWith(runs, runId) {
  return (runs || []).find((r) => r && r.run_id === runId) || null;
}

// 그 run 이 끝난 뒤에 끝난 analyze run 들 — 있으면 그 칸의 모집단은 이미 지금의 need_mention 이
// 아니다. `analyze polarity` 는 (src, month) 단위로 지우고 다시 넣어(analysis/polarity/pipeline.py 의
// NEED_DELETE) 시간창도 워터마크도 남기지 않으므로, 복원은 되지 않는다.
export function rewritersAfter(runs, runId) {
  const base = runWith(runs, runId);
  if (!base || !base.finished_at) return [];
  const after = new Date(base.finished_at).getTime();
  return (runs || [])
    .filter((r) => r && r.run_id !== runId && r.finished_at && String(r.note || '').startsWith(REWRITER))
    .filter((r) => new Date(r.finished_at).getTime() > after)
    .sort((a, b) => new Date(a.finished_at) - new Date(b.finished_at));
}

// 모르는 run 은 재현 가능으로 눕히지 않는다 — 조용히 틀린 목록을 보이는 것이 안 보이는 것보다 나쁘다.
export function reproducible(runs, runId) {
  const base = runWith(runs, runId);
  if (!base || !base.finished_at) return false;
  return rewritersAfter(runs, runId).length === 0;
}

// PostgREST 의 in. 목록. 값에 따옴표가 들어올 일은 없지만(versioning.md 의 두 형식) 이스케이프는 둔다.
function inList(values) {
  return `(${values.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(',')})`;
}

const ROLLUP_SCOPE = 'all';

// metrics_need 한 칸 = PK (run_id, scope, need_key, month, product_ref). 그 칸을 만든 언급들을 고르는
// 필터는 모집단(extractor_version) + 그 칸의 축이다.
//
// 빈 month 와 빈 product_ref 는 필터가 아니다 — 그 칸이 '전 기간 · 전 제품' 이라는 뜻이라, 빈 값으로
// 걸면 언급이 하나도 안 걸린다(언급에는 '전 기간' 이라는 값이 없다).
export function needCellFilters(cell, run) {
  if (!cell) return [];
  const extractors = extractorsOf(run);
  if (extractors.length === 0) return [];
  const filters = [
    { column: 'kind', op: 'eq', value: 'need' },
    { column: 'extractor_version', op: 'in', value: inList(extractors) },
  ];
  // A17: scope='all' 롤업만 needs.need_key.canonical 로 접힌다. 그 칸을 raw need_key 로 거르면
  // 대표 이름으로 접힌 동의어 언급들이 통째로 빠진다.
  if (cell.scope === ROLLUP_SCOPE) {
    filters.push({ column: 'need_key_rollup', op: 'eq', value: cell.need_key });
  } else {
    filters.push({ column: 'category', op: 'eq', value: cell.scope });
    filters.push({ column: 'need_key', op: 'eq', value: cell.need_key });
  }
  if (cell.month) filters.push({ column: 'month', op: 'eq', value: cell.month });
  if (cell.product_ref) filters.push({ column: 'product_axis', op: 'eq', value: cell.product_ref });
  return filters;
}

// metrics_wish 의 scope 는 카테고리가 아니라 바람의 종류다(WISH_SCOPES, analysis/aggregate/__init__.py).
const WISH_CLASS = { 'wish:a': 'a', 'wish:b': 'b', 'wish:a:format×attr': 'a' };
const WISH_AXES = [['format', 'format_first'], ['attribute', 'attribute_first'], ['brand', 'brand']];

// metrics_wish 한 칸 = PK (run_id, scope, format, attribute, brand). 빈 축은 값이 아니라 marginal
// 이므로 걸지 않는다 — 걸면 그 축이 실제로 빈 언급만 남아 칸의 mentions 와 목록 길이가 어긋난다.
export function wishCellFilters(cell, run) {
  if (!cell) return [];
  const wishClass = WISH_CLASS[cell.scope];
  const extractors = extractorsOf(run);
  if (!wishClass || extractors.length === 0) return [];
  const filters = [
    { column: 'kind', op: 'eq', value: 'wish' },
    { column: 'extractor_version', op: 'in', value: inList(extractors) },
    { column: 'wish_class', op: 'eq', value: wishClass },
  ];
  for (const [key, column] of WISH_AXES) {
    if (cell[key]) filters.push({ column, op: 'eq', value: cell[key] });
  }
  return filters;
}

// 원문 표가 있는 갈래만 수집분까지 내려간다. yt_transcript 와 naver_blog, 그리고 사이트를 모르는
// wish 리뷰는 mention_lineage 에서 이미 doc_kind 가 없고 여기서도 내려갈 자리가 없다.
const DRILLABLE = new Set(['review', 'yt_comment']);

export function documentFilters(mention) {
  if (!mention || !DRILLABLE.has(mention.src) || !mention.doc_key) return [];
  const filters = [{ column: 'src', op: 'eq', value: mention.src }];
  if (mention.site) filters.push({ column: 'site', op: 'eq', value: mention.site });
  if (mention.doc_parent) filters.push({ column: 'doc_parent', op: 'eq', value: mention.doc_parent });
  filters.push({ column: 'doc_key', op: 'eq', value: mention.doc_key });
  return filters;
}

const docId = (r) => [r.src, r.site, r.doc_parent, r.doc_key].join(' ');

// 후보가 여럿인 문서를 한 덩어리로 묶되 **줄이지 않는다** — 리뷰에서 수집 run 으로 가는 길은
// captured_at 으로만 이어져 34퍼센트가 후보 2~5개, 10퍼센트가 미상이다(#144 실측). 하나로 찍거나
// 숨기는 것이 더 나쁘다는 것이 사용자 결정이고, 그래서 이 함수는 rows 를 그대로 들고 순서만 고정한다.
export function groupByDocument(rows) {
  const groups = new Map();
  for (const r of rows || []) {
    const id = docId(r);
    if (!groups.has(id)) {
      groups.set(id, {
        src: r.src, site: r.site, doc_parent: r.doc_parent, doc_key: r.doc_key,
        match: r.match, candidate_count: r.candidate_count, rows: [],
      });
    }
    groups.get(id).rows.push(r);
  }
  for (const g of groups.values()) {
    g.rows.sort((a, b) => (a.candidate_rank || 0) - (b.candidate_rank || 0));
  }
  return [...groups.values()];
}

// 화면이 읽는 한 줄. 세 값이 다른 사실이라 문구도 셋이다.
export function describeMatch(match, count) {
  if (match === 'single') return '수집 run 확정';
  if (match === 'candidate') return `후보 수집 run ${count}개 — 어느 것인지 기록이 없다`;
  return '수집 run 미상 — captured_at 에 맞는 run 행이 없다';
}
