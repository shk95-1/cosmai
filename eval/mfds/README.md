# eval/mfds — the MFDS cosmetic registration ledger (snapshot, not re-collected)

| file | rows | columns | origin |
|---|---|---|---|
| `mfds_items_v1.csv` | 4,735 (+ 1 header line) | `COSMETIC_REPORT_SEQ` · `ITEM_NAME` · `ENTP_NAME` · `report_date` | `rag/mfds_items.csv` of youtube-data-collector at tag **v0.4.0** (`76db718`) |

**Snapshot, taken 2026-08-27** (the v0.4.0 tag; the file itself last changed in `2146d1b`). The newest
report date in it is **2026-08-20** and the oldest is 2008-10-30.

This is a **reference table, not a collection target**. cosmai#73 decided that ydc's external
ingredient CSV is not imported because the collector gathers that; the registration ledger is
different in kind — it is the official MFDS filing record, an authority we cross-check against
rather than an observation we could re-collect. So the rows are copied here verbatim, byte for byte,
and nothing in this repository re-derives, re-collects or edits them.

**It is not updated.** The real ledger grows every day; this copy stops at 2026-08-20 and says so on
every row it loads (`needs.mfds_snapshot`, one row, carries the origin tag, the row count, the
maximum report date and `loaded_at`). Refreshing it would mean a collector against the MFDS open API,
which is a separate decision and is not made here — see `contracts/formats.md` for the sentence that
binds it.

**A refresh is a new snapshot, not a bigger file here.** The loader inserts with
`ON CONFLICT DO NOTHING`, so dropping a grown CSV in place would quietly file the new rows under
snapshot 1 while the stored row count and newest report date went on describing this file. It refuses
that: replacing this CSV means bumping `SNAPSHOT_ID` and the label in `db/seed/mfds.py`, which is a
change somebody reviews. For the same reason a filing whose values changed under a report number
already loaded is neither re-entered nor updated — this ledger takes it that MFDS does not re-file
under a report number it has already used, and if that ever proves wrong the repair is a new
snapshot rather than a rerun.

Loading: `uv run python -m db.seed --only mfds` (see `db/seed/mfds.py`). What the ledger joins to
is re-measured by `uv run tool/measure-mfds-join`.

The values are Korean because the source ledger is Korean; `tool/checks/lang` allowlists `eval/`
for exactly this reason (data, not operating surface).
