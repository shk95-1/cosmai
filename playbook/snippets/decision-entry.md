<!-- origin: service/yt-scrapper/decisions/README.md:1-13 + decisions/001 (shape)
     reuse: ONE file docs/decisions.md; append an entry only after the failure happened and was measured; ≤10 lines each; delete the entry when its condition stops holding. -->
## <rule in one line>

- **Rule**: what to do / not do.
- **Cost paid**: date, numbers (e.g. "an 8× throughput drop in a 40-job sweep, invisible in a single job, the full suite, or a 10-job trial").
- **Condition to retire**: what fact changing makes this rule wrong (e.g. "once migrated to Postgres, the `readonly` distinction earns nothing" → delete the entry on migration day).
- **Enforcement**: hook | test path | none (a person greps).
