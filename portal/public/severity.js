// How bad one stage is — the ops table (#139) and the structure map (#143) call **the same function**.
//
// If two screens showed the same stage in different colors, neither could be trusted. So only one lives here.
// The judgement itself does not live here: needs.pipeline_health already decides freshness and
// last_run_status and hands them over (#138), and this file only holds the rule that folds the two into one color.

// Severity is **one scale**: 0 critical · 1 warning · 2 not run yet · 3 ok · 4 declared off.
// If the two columns each kept their own ranking and those rankings were mixed, the comparison would lose
// its meaning — "the worse of the two" only holds when both are measured on the same ruler.
const FRESHNESS_SEVERITY = { stalled: 0, late: 1, never: 2, ok: 3, disabled: 4 };
// yielded is a run that stepped aside for a source lock, not a failure (#78). blocked is 403·429 — a warning.
const STATUS_SEVERITY = { failed: 0, partial: 1, blocked: 1, yielded: 3, ok: 3 };

export const SEVERITY_CLASS = ['sev-critical', 'sev-warn', 'sev-idle', 'sev-ok', 'sev-muted'];

export function severityOf(row) {
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
