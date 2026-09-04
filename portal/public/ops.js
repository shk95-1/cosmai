// The judgement half of the ops screen — pure functions only. No DOM, no fetch, so portal/test can measure it directly
// (the same split as query/screens/render, tool/checks/js).
//
// The judgement itself does not live here. The needs.pipeline_health view already decides freshness and
// last_run_status and hands them over (#138) — if the screen judged again, it could disagree with tool/status
// or alerting. What this file does is arrange that judgement in *the order a person reads it*.

// Severity and color are carried by severity.js -- the structure map (#143) calls the same functions. If two
// screens showed the same stage in different colors, neither could be trusted. Re-exporting here is only for
// the screens and tests that are already calling this module.
export { severityOf, severityClass } from './severity.js';
import { severityOf } from './severity.js';

// The set the banner and the "problems-only" bundle count. Since the question the banner answers is "what is
// stuck right now," only two things belong here: it is not running · the last run failed outright.
//
// never and disabled are not included -- never is the honest mark of a stage that has not run yet (naver
// datalab·blog), not a failure, and a dashboard that is always red is one nobody looks at anymore (#138).
//
// partial is not included either (#156). commerce:product is partial 3 days out of 4 while still collecting
// 84-86 of 89 -- counting that as a stuck stage would turn the banner red every day, and the trap #154 removed
// from the view would just move up one level to the screen. It does not disappear, though: severityOf marks it
// warning so the row still stands out, and the failed count stays in the request stats. It is true that a large
// failure *rate* should raise the banner, but there is no measurement yet to set that threshold -- a separate issue.
const BAD_FRESHNESS = new Set(['stalled', 'late']);
const BAD_STATUS = new Set(['failed']);

export function isProblem(row) {
  return BAD_FRESHNESS.has(row.freshness) || BAD_STATUS.has(row.last_run_status);
}

export function problems(rows) {
  return sortByWorst((rows || []).filter(isProblem));
}

// The number the banner's one line counts. If this number and the problem bundle's row count ever diverge
// anywhere on screen, one of them is lying, so both places share the same predicate (isProblem).
export function problemCount(rows) {
  return problems(rows).length;
}

// The worse of the two values. The screen picks its color from this but displays **both** values -- collapsing
// them into one would make "dead since it failed 3 days ago" look the same as "just failed but still inside the interval" (#138).
export function sortByWorst(rows) {
  // Ties are broken by stage_key -- if rows of the same severity shifted order with the response order, the same
  // screen would look different every morning.
  return [...(rows || [])].sort(
    (a, b) => severityOf(a) - severityOf(b) || (a.stage_key < b.stage_key ? -1 : a.stage_key > b.stage_key ? 1 : 0),
  );
}

// The order of arms is fixed. Reordering arms by row count or severity would put them in a different spot every day.
export const ARM_ORDER = ['commerce', 'youtube', 'naver', 'analyze'];

export function byArm(rows) {
  const live = (rows || []).filter((r) => r.freshness !== 'disabled');
  return ARM_ORDER
    .map((arm) => ({ arm, rows: sortByWorst(live.filter((r) => r.arm === arm)) }))
    .filter((g) => g.rows.length > 0);
}

// Declared not to run. Disappearing from the list and quietly not running are different, so this is separated out.
export function disabled(rows) {
  return sortByWorst((rows || []).filter((r) => r.freshness === 'disabled'));
}

const MINUTE = 60, HOUR = 3600, DAY = 86400;

// The absolute time stays as a subtitle while the main text is relative -- "2026-08-24 03:00 UTC" leaves it to a
// person to work out whether it is late. The reference TZ is decided by #89, and this spot's absolute-time wording changes with it then.
export function relativeTime(at, now) {
  if (!at) return '—';
  const seconds = Math.floor((now.getTime() - new Date(at).getTime()) / 1000);
  if (seconds < 0) return '방금';           // a clock skew is never drawn as the future
  if (seconds < MINUTE) return `${seconds}초 전`;
  if (seconds < HOUR) return `${Math.floor(seconds / MINUTE)}분 전`;
  if (seconds < DAY) return `${Math.floor(seconds / HOUR)}시간 전`;
  return `${Math.floor(seconds / DAY)}일 전`;
}

// A human-readable interval. PostgREST hands back an interval as a string like 'HH:MM:SS' or 'N days'.
export function describeInterval(text) {
  if (!text) return '—';
  const iso = /^(?:(\d+) days?\s*)?(?:(\d+):(\d+):(\d+))?$/.exec(String(text).trim());
  if (!iso) return String(text);            // a shape we don't recognize, like mon, is shown as-is
  const [, days, hours, minutes] = iso;
  if (days) return Number(days) === 1 ? '매일' : `${days}일마다`;
  if (hours && Number(hours) > 0) return Number(hours) === 1 ? '매시' : `${Number(hours)}시간마다`;
  if (minutes && Number(minutes) > 0) return `${Number(minutes)}분마다`;
  return String(text);
}
