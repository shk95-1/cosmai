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

export function productRows(need, runId, limit = 20) {
  return sortRows((need || []).filter((r) => r.run_id === runId && r.product_ref !== ''), 'unresolved', 'desc').slice(0, limit);
}

// run 하나를 "#id (note, versions)"로 적는다 — 손 재집계 직후 화면이 그 run을 골랐는지
// 눈으로 확인하려면 run_id 만으로는 부족하다(#87 완료 기준).
function describeRun(run) {
  if (!run) return null;
  const versions = run.versions && typeof run.versions === 'object' ? JSON.stringify(run.versions) : '';
  const detail = [run.note, versions].filter(Boolean).join(', ');
  return detail ? `#${run.run_id} (${detail})` : `#${run.run_id}`;
}

export function runCaption(needRun, wishRun) {
  const need = describeRun(needRun);
  const wish = describeRun(wishRun);
  if (need === null && wish === null) return '데이터 없음';
  return `need run ${need ?? '없음'} · wish run ${wish ?? '없음'}`;
}
