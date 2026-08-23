// 화면별로 "어떤 run의 어떤 행"을 보여줄지 고르는 순수 함수. app.js 가 DOM에
// 꽂기 전에 이 판단을 분리해 둔 것은 이 판단 자체가 틀리기 쉬워서다.
import { latestRunId, sortRows } from './query.js';

// need·wish 는 같은 run_id를 공유하지 않는다(에픽 #16 §1단계 판정 4, 시드는
// slice-suncare/p1/p9 별로 run이 따로다) — 표마다 latestRunId를 따로 구한다.
// 하나의 runId로 두 표를 거르면 두 run이 어긋나는 순간 한쪽이 통째로 빈다
// (수정 라운드 1: 시드에서 need=2, wish=3이라 이 버그가 화면 2를 항상 비웠다).
export function latestRuns(need, wish) {
  return { needRunId: latestRunId(need), wishRunId: latestRunId(wish) };
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

export function runCaption(needRunId, wishRunId) {
  if (needRunId === null && wishRunId === null) return '데이터 없음';
  return `need run #${needRunId} · wish run #${wishRunId}`;
}
