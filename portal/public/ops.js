// 운영 관제 페이지의 판단 부분 — 순수 함수만. DOM 도 fetch 도 없어 portal/test 가 그대로 잰다
// (query/screens/render 와 같은 분리, tool/checks/js).
//
// 판정 자체는 여기 없다. needs.pipeline_health 뷰가 freshness 와 last_run_status 를 이미 정해서
// 준다(#138) — 화면이 다시 판정하면 tool/status 나 알림과 답이 갈린다. 여기가 하는 것은 그
// 판정을 *사람이 읽는 순서*로 놓는 일이다.

// 심각도는 **하나의 척도**다: 0 위급 · 1 주의 · 2 아직 안 돎 · 3 정상 · 4 선언상 꺼짐.
// 두 컬럼이 각자의 등수를 갖고 그 등수를 섞으면 비교가 뜻을 잃는다 -- 같은 자로 재야
// "둘 중 나쁜 쪽" 이 성립한다.
const FRESHNESS_SEVERITY = { stalled: 0, late: 1, never: 2, ok: 3, disabled: 4 };
// yielded 는 소스 락에 밀려 물러난 run 이라 고장이 아니다(#78). blocked 는 403·429 -- 주의다.
const STATUS_SEVERITY = { failed: 0, partial: 1, blocked: 1, yielded: 3, ok: 3 };

// 배너와 '문제만 모은 덩어리'가 세는 집합. 배너가 답하는 질문은 "지금 무엇이 막혔나" 이므로
// 여기 드는 것은 둘뿐이다: 안 돌고 있다 · 마지막 run 이 통째로 실패했다.
//
// never 와 disabled 는 안 든다 -- never 는 아직 안 돈 단계(naver datalab·blog)의 정직한
// 표시이지 고장이 아니고, 항상 빨간 대시보드는 아무도 안 보게 된다(#138).
//
// partial 도 안 든다(#156). commerce:product 는 4일 중 3일이 partial 이면서 89 중 84~86 을
// 걷는다 -- 그것을 막힌 단계로 세면 배너가 매일 빨갛고, #154 가 뷰에서 없앤 함정이 화면에서
// 한 칸 위로 옮겨질 뿐이다. 사라지지는 않는다: severityOf 가 '주의' 로 두어 행이 눈에 띄고
// failed 건수는 요청 통계에 남는다. 실패 *비율* 이 크면 배너에 서야 한다는 것은 맞지만, 그
// 문턱을 정할 실측이 아직 없다 -- 별건.
const BAD_FRESHNESS = new Set(['stalled', 'late']);
const BAD_STATUS = new Set(['failed']);

export function isProblem(row) {
  return BAD_FRESHNESS.has(row.freshness) || BAD_STATUS.has(row.last_run_status);
}

export function problems(rows) {
  return sortByWorst((rows || []).filter(isProblem));
}

// 배너 한 줄이 세는 수. 화면 전체에서 이 수와 '문제 덩어리'의 행 수가 달라지면 둘 중 하나가
// 거짓말이므로, 같은 술어(isProblem)를 두 자리가 나눠 쓴다.
export function problemCount(rows) {
  return problems(rows).length;
}

// 두 값 중 나쁜 쪽. 화면은 이것으로 색을 고르되 두 값을 **둘 다** 표시한다 -- 하나로 접으면
// "3일 전에 실패한 뒤로 죽어 있다" 와 "방금 실패했지만 아직 주기 안" 이 같아 보인다(#138).
export function severityOf(row) {
  // 모르는 값과 없는 값(아직 안 돈 단계의 last_run_status)은 판단에서 빠진다 -- 정상으로도
  // 고장으로도 세지 않는다. 그러면 남은 한쪽이 그 행의 심각도를 결정한다.
  const f = FRESHNESS_SEVERITY[row.freshness];
  const s = STATUS_SEVERITY[row.last_run_status];
  return Math.min(f === undefined ? 99 : f, s === undefined ? 99 : s);
}

export function sortByWorst(rows) {
  // stage_key 로 동률을 깬다 -- 같은 심각도의 행 순서가 응답 순서에 따라 흔들리면 매일 아침
  // 같은 화면이 다르게 보인다.
  return [...(rows || [])].sort(
    (a, b) => severityOf(a) - severityOf(b) || (a.stage_key < b.stage_key ? -1 : a.stage_key > b.stage_key ? 1 : 0),
  );
}

// 팔의 순서는 고정이다. 행 수나 심각도로 팔을 재정렬하면 매일 다른 자리를 봐야 한다.
export const ARM_ORDER = ['commerce', 'youtube', 'naver', 'analyze'];

export function byArm(rows) {
  const live = (rows || []).filter((r) => r.freshness !== 'disabled');
  return ARM_ORDER
    .map((arm) => ({ arm, rows: sortByWorst(live.filter((r) => r.arm === arm)) }))
    .filter((g) => g.rows.length > 0);
}

// 선언상 안 도는 것. 목록에서 사라지는 것과 조용히 안 도는 것은 다르므로 따로 낸다.
export function disabled(rows) {
  return sortByWorst((rows || []).filter((r) => r.freshness === 'disabled'));
}

const MINUTE = 60, HOUR = 3600, DAY = 86400;

// 절대시각은 부제로 남기고 본문은 상대시각이다 -- "2026-08-24 03:00 UTC" 는 늦었는지를 사람이
// 계산하게 만든다. 기준 TZ 는 #89 가 정하고, 그때 이 자리의 절대시각 표기도 함께 고친다.
export function relativeTime(at, now) {
  if (!at) return '—';
  const seconds = Math.floor((now.getTime() - new Date(at).getTime()) / 1000);
  if (seconds < 0) return '방금';           // 시계 어긋남을 미래로 그리지 않는다
  if (seconds < MINUTE) return `${seconds}초 전`;
  if (seconds < HOUR) return `${Math.floor(seconds / MINUTE)}분 전`;
  if (seconds < DAY) return `${Math.floor(seconds / HOUR)}시간 전`;
  return `${Math.floor(seconds / DAY)}일 전`;
}

// 사람이 읽는 주기. PostgREST 는 interval 을 'HH:MM:SS' 나 'N days' 같은 문자열로 준다.
export function describeInterval(text) {
  if (!text) return '—';
  const iso = /^(?:(\d+) days?\s*)?(?:(\d+):(\d+):(\d+))?$/.exec(String(text).trim());
  if (!iso) return String(text);            // mon 처럼 우리가 모르는 모양은 그대로 보여준다
  const [, days, hours, minutes] = iso;
  if (days) return Number(days) === 1 ? '매일' : `${days}일마다`;
  if (hours && Number(hours) > 0) return Number(hours) === 1 ? '매시' : `${Number(hours)}시간마다`;
  if (minutes && Number(minutes) > 0) return `${Number(minutes)}분마다`;
  return String(text);
}
