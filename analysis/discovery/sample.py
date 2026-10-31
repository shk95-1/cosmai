"""Category-agnostic behavior probes over canonical local source evidence, read-only."""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path

from psycopg.rows import dict_row

from analysis.discovery.core import fingerprint, freeze, validate_snapshot
from analysis.retrieval.corpus import review_doc_id
from db.runtime import connect, runtime_url

CUES = Path(__file__).with_name("cues.csv")
# These are access boundaries, not a declared target market or a product category selector.
COMMERCE = ("daisomall", "glowpick", "oliveyoung")
YOUTUBE = "youtube_comment"

COMMERCE_BASE = """
SELECT r.review_key AS source_item_id, r.body AS text, r.author_hash,
       r.written_at::text AS published_at, p.url, p.name AS product_name,
       r.product_key, r.captured_at::text AS collected_at
FROM trend_radar.review r LEFT JOIN trend_radar.product p
ON p.source=r.source AND p.product_key=r.product_key
WHERE r.source=%s
"""
# A comment can be present in archive and live snapshots. Take ONE latest source item before
# sampling. Never count snapshot duplicates as consumers, and do not use publisher channel_id.
YOUTUBE_BASE = """
SELECT DISTINCT ON (source_item_id) source_item_id, text,
       source_metadata->>'author_channel_hash' AS author_hash,
       published_at::text AS published_at, url, parent_item_id,
       source_run, collected_at::text AS collected_at
FROM needs.corpus_document d WHERE source='youtube_comment'
ORDER BY source_item_id, d.collected_at DESC, snapshot_id DESC
"""


def sample_local(per_cue: int = 3, *, exclude_sample: dict | None = None) -> dict:
    if not 1 <= per_cue <= 20:
        raise ValueError("per-cue limit must be between 1 and 20")
    excluded: dict[str, list[str]] = {}
    if exclude_sample is not None:
        validate_snapshot(exclude_sample)
        for document in exclude_sample["documents"]:
            source = document["source"]
            item = document["source_item_id"]
            expected = f"{YOUTUBE}:{item}" if source == YOUTUBE else review_doc_id(source, item)
            if source not in (*COMMERCE, YOUTUBE) or document["doc_id"] != expected:
                raise ValueError("exclusion needs canonical source-local document coordinates")
            excluded.setdefault(source, []).append(item)
    with CUES.open(newline="") as f:
        cues = list(csv.DictReader(f))
    manifest: dict = {
        "started_at": datetime.now(UTC).isoformat(),
        "sampling": "per-source/per-cue stable MD5 source-ID order; deduplicated IDs",
        "per_cue": per_cue,
        "cue_sha256": fingerprint(cues),
        "cues": cues,
        "coverage": [],
        "failures": [],
        "paid_calls": 0,
        "gpu_hours": 0,
        "bias": [
            "existing Korean commerce and channel-panel capture; no global coverage",
            "cue lexicon misses unmentioned/other-language workarounds",
            "live autocommit reads are not one transaction-wide DB snapshot",
            "null commerce URLs retain table/source/review-key coordinates",
            "latest source snapshot only; normalized copies deduplicated at selection",
            "YouTube uses needs.corpus_document; later unprojected raw comments are outside this sample",
        ],
    }
    documents: dict[str, dict] = {}
    if exclude_sample is not None:
        manifest["exclusion"] = {
            "snapshot_sha256": exclude_sample["snapshot_sha256"],
            "document_count": len(exclude_sample["documents"]),
            "document_ids_sha256": fingerprint(sorted(d["doc_id"] for d in exclude_sample["documents"])),
        }
    with connect(runtime_url()) as conn:
        conn.autocommit = True
        conn.execute("SET default_transaction_read_only = on")
        for source in (*COMMERCE, YOUTUBE):
            base = YOUTUBE_BASE if source == YOUTUBE else COMMERCE_BASE
            params = () if source == YOUTUBE else (source,)
            if exclude_sample is not None:
                # Exclude before counting and LIMIT so prior hits cannot consume the new quota.
                base = "SELECT * FROM (" + base + ") prior WHERE NOT (source_item_id = ANY(%s))"
                params = (*params, sorted(excluded.get(source, [])))
            coverage: dict = {"source": source, "cues": []}
            try:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        "SELECT count(*) AS documents, min(published_at) AS first_date, "
                        "max(published_at) AS last_date FROM (" + base + ") evidence",
                        params,
                    )
                    coverage.update(cur.fetchone() or {})
                    for cue in cues:
                        cur.execute(
                            "SELECT count(*) AS matching FROM (" + base + ") evidence WHERE text ~* %s",
                            (*params, cue["pattern"]),
                        )
                        matching = (cur.fetchone() or {})["matching"]
                        cur.execute(
                            "SELECT * FROM (" + base + ") evidence WHERE text ~* %s "
                            "ORDER BY md5(source_item_id), source_item_id LIMIT %s",
                            (*params, cue["pattern"], per_cue),
                        )
                        rows = cur.fetchall()
                        coverage["cues"].append(
                            {"cue": cue["cue"], "matching": matching, "sampled": len(rows)}
                        )
                        for row in rows:
                            doc_id = (
                                f"{YOUTUBE}:{row['source_item_id']}"
                                if source == YOUTUBE
                                else review_doc_id(source, row["source_item_id"])
                            )
                            if doc_id not in documents:
                                row.update(doc_id=doc_id, source=source, cues=[])
                                documents[doc_id] = row
                            documents[doc_id]["cues"].append(cue["cue"])
            except Exception as exc:
                # Driver errors may contain SQL/connection details. Keep only the error class.
                manifest["failures"].append(
                    {
                        "source": source,
                        "error_class": type(exc).__name__,
                        "action": "partial source sample retained; coverage incomplete",
                    }
                )
            manifest["coverage"].append(coverage)
    manifest["finished_at"] = datetime.now(UTC).isoformat()
    return freeze(manifest, list(documents.values()))


def main() -> None:
    import argparse
    import json
    import os

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exclude-sample", type=Path)
    parser.add_argument("--per-cue", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.exclude_sample and args.output.resolve() == args.exclude_sample.resolve():
        parser.error("output cannot overwrite the prior evidence snapshot")
    if args.output.exists():
        parser.error("output already exists; preserve the frozen capture")
    try:
        previous = json.loads(args.exclude_sample.read_text()) if args.exclude_sample else None
        result = sample_local(args.per_cue, exclude_sample=previous)
        # Exclusive creation preserves existing evidence and never exposes hashes in stdout.
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(result, output, ensure_ascii=True, indent=2)
            output.write("\n")
    except Exception as exc:
        parser.exit(2, f"{type(exc).__name__}: discovery capture blocked\n")
    print(
        json.dumps(
            {
                "documents": len(result["documents"]),
                "snapshot_sha256": result["snapshot_sha256"],
                "failures": result["manifest"]["failures"],
            }
        )
    )


if __name__ == "__main__":
    main()
