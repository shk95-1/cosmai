// Pure functions that pick "which run's which rows" each screen shows. Splitting this judgement out
// before app.js wires it into the DOM is because this judgement itself is easy to get wrong.
import { okRunsByRecency, sortRows } from './query.js';

// need·wish 는 같은 run_id를 공유하지 않는다(에픽 #16 §1단계 판정 4, 시드는
// slice-suncare/p1/p9) — each table's own run is found separately.
// Filtering both tables by one runId would leave one entirely empty the moment the two runs diverge
// (fix round 1: with the seed's need=2, wish=3, this bug always emptied screen 2).
//
// #87: scans runs (analysis_run) most-recent-first by finished_at and picks the first run_id that
// actually left rows in that table (need/wish) — since it is picked by "when it finished," not by
// the max run_id, a smaller run_id reused by a manually-run aggregate is still caught as the latest.
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

// Product-axis rows come out one set per scope (per category + the 'all' rollup, #41). If the same product
// were caught twice, once in its own category and once in 'all,' the top 20 would fill with duplicates, so
// only the rollup is read when it exists — 'all' carries every product once, after synonyms are folded. A
// run run narrowed by --scope has no 'all,' so in that case the scope that exists is used as-is.
export function productRows(need, runId, limit = 20) {
  // month is '' for both the category sum and the product axis. Once the month axis exists, those rows must never mix in here.
  const rows = (need || []).filter((r) => r.run_id === runId && r.product_ref !== '' && r.month === '');
  const rolled = rows.filter((r) => r.scope === 'all');
  return sortRows(rolled.length ? rolled : rows, 'unresolved', 'desc').slice(0, limit);
}

// finished_at is an ISO string like '2026-08-26T05:01:31.074893+00:00'. Parsing it with Date and printing
// local time would write the same run's time differently on every machine, so it is sliced straight from the string instead.
function stampOf(finishedAt) {
  const m = /^\d{4}-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/.exec(String(finishedAt || ''));
  return m ? `${m[1]}-${m[2]} ${m[3]}:${m[4]}` : '';
}

// versions carries even nested values like lexicon and ate up four header lines (#122) — the summary
// writes only the one pair that identifies the pipeline, leaving the rest to the collapsible detail.
// extractor is read first because what the screen's numbers were extracted with is the first thing anyone wants to know.
const HEADLINE_VERSION = 'extractor';
function headlineVersion(versions) {
  if (!versions || typeof versions !== 'object') return '';
  const keys = Object.keys(versions);
  const key = keys.includes(HEADLINE_VERSION)
    ? HEADLINE_VERSION
    : keys.find((k) => typeof versions[k] === 'string');
  return key ? `${key} ${versions[key]}` : '';
}

// Turns one run into a single line, "#24 · 08-26 05:01 · extractor rule-v2.3" — right after a manual
// reaggregation, run_id alone is not enough to confirm by eye that the screen picked that run (#87's done criterion).
export function runBrief(run) {
  if (!run) return '없음';
  return [`#${run.run_id}`, stampOf(run.finished_at), headlineVersion(run.versions)]
    .filter(Boolean).join(' · ');
}

function runDetail(name, run) {
  const versions = run.versions && typeof run.versions === 'object' ? JSON.stringify(run.versions) : '';
  return [`${name} run #${run.run_id}`, run.note, versions].filter(Boolean).join('\n');
}

// Splits the one line for the header (summary) from the full text collapsed inside <details> (detail) —
// app.js wires the two into the DOM. The split itself must be a pure function for a test to attach to it.
export function runCaptionParts(needRun, wishRun) {
  if (!needRun && !wishRun) return { summary: '데이터 없음', detail: '' };
  // In practice one analyze run writes both tables (run #24) — writing the same thing twice would leave
  // the very reason it was being collapsed (length) unresolved.
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

// A select's first item is the alphabetically first scope, so an accidental category like "01 > mask
// pack > sheet pack" ended up as the first screen (#122). When the 'all' rollup exists it is the whole
// picture; otherwise the scope with the most rows is where that run actually looked. A tie is broken
// alphabetically — if the first screen changed on every refresh, there would be no telling what was being looked at.
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

// Guard against division by zero: the ratio is null when the denominator is missing or 0 — flattening it
// to 0 would make "a need with no denominator" and "a need that is truly 0" look the same, and the scatter would lie.
export function safeRatio(numerator, denominator) {
  const n = Number(numerator);
  const d = Number(denominator);
  if (numerator === null || numerator === undefined || !Number.isFinite(n)) return null;
  if (denominator === null || denominator === undefined || !Number.isFinite(d) || d === 0) return null;
  return n / d;
}

// The rows screen 4 uses: three ratios laid on top of the category-sum rows. Persistence (month)·spread
// (product) are the scatter's x·y. new_ratio divides unresolved_new (the unresolved rate for new products
// only, 002_audit_additive A4) by the overall unresolved rate, so 1 (=100%) means "new products are at the
// same level" and it can exceed 1 — since it is not 0-1, it is not used as a scatter axis. The original column is kept as-is.
export function needCharacterRows(need, runId, scope) {
  return needRowsForScope(need, runId, scope).map((r) => ({
    ...r,
    persist_month_ratio: safeRatio(r.persist_months, r.persist_months_total),
    persist_product_ratio: safeRatio(r.persist_products, r.persist_products_total),
    new_ratio: safeRatio(r.unresolved_new, r.unresolved),
  }));
}

// A scope where yt_neg/yt_pos are all 0 means there is no YouTube collection — a phrase must show instead
// of an empty chart to distinguish "0 mentions" from "that source was never collected."
export function hasYoutubeMentions(rows) {
  return (rows || []).some((r) => (Number(r.yt_neg) || 0) > 0 || (Number(r.yt_pos) || 0) > 0);
}

// A row whose value is null (denominator 0) is dropped from the bar chart — otherwise Number(null)||0
// would masquerade as a 0% bar, drawing the nonexistent fact "there are no new ones at all."
export function rowsWithValue(rows, key) {
  return (rows || []).filter((r) => {
    const v = r[key];
    return v !== null && v !== undefined && v !== '' && Number.isFinite(Number(v));
  });
}

// ---- Screen 3: product name -----------------------------------------------------

// metrics_need only carries ref, so screen 3's bar label used to be 'oy:A000000149577'.
// needs.product_ref has brand·name and is also in the anon whitelist (#11's input).
// name 은 '[8월올영픽] … 80ml 1+1 기획' 처럼 기획 문구와 용량을 달고 있어 라벨에는
// name_norm, which strips that, and falls back to name only when it is missing.
export function productNameIndex(rows) {
  const index = new Map();
  for (const r of rows || []) {
    if (!r || !r.product_ref) continue;
    index.set(r.product_ref, { brand: r.brand || '', name: r.name_norm || r.name || '' });
  }
  return index;
}

// A ref not in the catalog is left as the ref. A mention the linker could not attach ends up with the
// site's original key as its ref as-is (aggregate's _product), and inventing a name for that spot would
// have the screen assert a link the pipeline never made.
export function productLabel(ref, index) {
  const hit = index && typeof index.get === 'function' ? index.get(ref) : undefined;
  if (!hit) return String(ref);
  const parts = [hit.brand, hit.name].filter(Boolean);
  return parts.length ? parts.join(' · ') : String(ref);
}

// A bar's label slot has a fixed width, so a long name overflows onto the next bar — a truncated label is
// drawn instead and the full name is kept in <title> (on hover).
export function truncateLabel(text, max = 20) {
  const s = String(text);
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

// Adds two labels (full and truncated) onto screen 3's rows. The original column is kept as-is —
// product_ref must remain in the table too so a spot the linker could not attach can still be recognized.
export function withProductNames(rows, index, max = 20) {
  return (rows || []).map((r) => {
    const product = productLabel(r.product_ref, index);
    return { ...r, product, product_short: truncateLabel(product, max) };
  });
}

// ---- Screen 5: period (month) axis (#130) ---------------------------------------------

// Month rows are attached only to the category sum (#129: month <> '' and product_ref = ''). Although the
// query already narrows it that way, filtering again here guards against there being no guarantee the array
// this function receives is always that query's response — even one all-period row leaking in would read as one month's value.
function monthRowsOf(need, runId) {
  return (need || []).filter((r) => r && r.run_id === runId && r.month !== '' && r.month !== undefined
    && r.month !== null && r.product_ref === '');
}

// The cap on months drawn on the panel. Drawing all 90 months (measured 2013-08~2026-08) would make the
// panel 2,500px, exactly the height #122 removed from screen 1. Since the cap is a property of the panel,
// not the screen, this is the source of truth, and index.html's caption also reads this value through
// app.js — keeping two copies would let one change without the other, writing a promise the screen does not keep.
export const MONTH_LIMIT = 24;

// That (run·scope·need_key)'s month rows, ascending by month. Since month is a 'YYYY-MM' string,
// alphabetical order is chronological order. limit trims from the end (0 means all) — the table calls it with 0:
// there must be a place on screen to see a month the panel pushed off.
export function monthRows(need, runId, scope, needKey, limit = MONTH_LIMIT) {
  const rows = monthRowsOf(need, runId)
    .filter((r) => r.scope === scope && r.need_key === needKey)
    .sort((a, b) => (a.month < b.month ? -1 : a.month > b.month ? 1 : 0));
  return limit && rows.length > limit ? rows.slice(rows.length - limit) : rows;
}

// The need_keys that have month rows in that scope. This is both the list that fills the select and, when
// empty, the fact itself that "this scope has no month axis."
export function monthNeedKeys(need, runId, scope) {
  return [...new Set(monthRowsOf(need, runId).filter((r) => r.scope === scope).map((r) => r.need_key))].sort();
}

// "There are no month rows" and "0 that month" are different facts — the former becomes wording
// (the aggregation has not produced the month axis yet, or never ran that scope), the latter becomes a
// zero-width bar. It is the same distinction hasYoutubeMentions makes on the YouTube axis.
export function hasMonthRows(need, runId, scope) {
  return monthNeedKeys(need, runId, scope).length > 0;
}
