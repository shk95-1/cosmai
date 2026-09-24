// How bad one stage is — the ops table (#139) and the structure map (#143) call **the same function**.
//
// If two screens showed the same stage in different colors, neither could be trusted. So only one lives here.
// The judgement itself does not live here: needs.pipeline_health already decides freshness and
// last_run_status and partial_excessive and hands them over (#138, #157); this file only folds them into one color.

// Severity is **one scale**: 0 critical · 1 warning · 2 not run yet · 3 ok · 4 declared off.
// The two enums share a scale; partial_excessive promotes a majority-missed partial to critical.
const FRESHNESS_SEVERITY = { stalled: 0, late: 1, never: 2, ok: 3, disabled: 4 };
// yielded is a run that stepped aside for a source lock, not a failure (#78). A cancelled-only queue
// bucket likewise completed no work, so it is idle rather than failed (#112). blocked is 403·429 — a warning.
const STATUS_SEVERITY = { failed: 0, partial: 1, blocked: 1, cancelled: 2, yielded: 3, ok: 3 };

export const SEVERITY_CLASS = ['sev-critical', 'sev-warn', 'sev-idle', 'sev-ok', 'sev-muted'];

export function severityOf(row) {
  if (row.partial_excessive === true) return 0;
  // An unknown value and a missing value (last_run_status of a stage that has not run yet) are excluded
  // from the judgement — counted neither as ok nor as failed. The remaining side then decides that row's severity.
  const f = FRESHNESS_SEVERITY[row.freshness];
  const s = STATUS_SEVERITY[row.last_run_status];
  return Math.min(f === undefined ? 99 : f, s === undefined ? 99 : s);
}

/** The CSS class attached to a severity. Left as 'not run yet' when no known value applies. */
export function severityClass(row) {
  return SEVERITY_CLASS[severityOf(row)] || 'sev-idle';
}
