# launch — the hand-labelled launch-month sample (#283 work 6, read by #125)

`launch_sample.csv` is **empty on purpose**: it ships with its header and no rows, because a launch
month nobody knows is not a label. A person fills it, thirty to fifty products, and
`tool/launch-coverage` scores every axis of the launch-evidence ledger against it.

| column | what goes in it |
|---|---|
| `product_ref` | **required** — the canonical ref (`needs.product_ref`). It is what the score joins on, and a row without one is refused rather than silently dropped |
| `brand` · `name` | the product as a person names it, so the file can be read back. Nothing joins on them |
| `launch_month` | `YYYY-MM`, the month the product went on sale. A day is not asked for: nobody has one |
| `source_of_knowledge` | where the month came from — a brand page, a press release, a magazine, the person's own memory |
| `note` | anything that qualifies the month (a renewal, a re-release, a set) |

`product_ref` and `launch_month` are both required and a row missing either is refused by the loader
rather than silently scored as a miss. The refs to choose from come out of the database read-only:

```sql
SET default_transaction_read_only = on;
SELECT product_ref, brand, name FROM needs.product_ref ORDER BY brand, name;
```

**What the number is for.** Each axis's hit rate against this file is what decides whether the next
axis is worth building, and the gap between the metric computed with and without `single_axis`
products is how far the MFDS join can be trusted (#282, the user's option-(B) decision). Until the
file has rows, `tool/launch-coverage` prints coverage alone and leaves the accuracy columns empty —
which is the honest state, not a failure.
