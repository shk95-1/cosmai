<!-- origin: service/trend-radar/docs/judgment-debt.md:1-11 (three bins) + Research_Paper/docs/judgment-debt.md (table form)
     reuse: three sections only, three lines per item (what / why / what would reopen it). Delete an item when it is resolved — git keeps history. No "resolution log" section. -->
# Judgment Debt

Things deliberately deferred, known limitations left in place, assumptions that need re-checking.
"Not done yet" and "decided not to do" are different — mixed together, the latter reads as
incomplete and gets quietly reverted. An item is three lines: **what / why / what would reopen it**.

## 1. Decided not to do (reversing this needs a decision)

| What was skipped | Why (one sentence, date · numbers) | Condition to revisit |
|---|---|---|
| Challenge bypass, fingerprint spoofing, proxy rotation | Overturning a site's refusal by technical means. Only the path where a human passes it once via `login` stays | None |
| | | |

## 2. Known operational limitations left as they are

| Limitation | Why (budget · measurement) | Condition to revisit |
|---|---|---|
| Hwahae HTTP 500 at 18% (measured 2026-08-23) | Server-side; 2 retries is enough | Exceeds 50%, or 3 consecutive days of 0 rows |
| | | |

## 3. Explored and found empty (distinct from not explored)

| What was explored | Date · method | Result |
|---|---|---|
| Daisomall AI summary | 2026-08-19, full sweep of detail responses | `data: []` |
| | | |
