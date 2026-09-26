"""Category-agnostic behavior probes over canonical local source evidence, read-only."""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path

from psycopg.rows import dict_row

from analysis.discovery.core import fingerprint, freeze
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
FROM needs.corpus_document WHERE source='youtube_comment'
ORDER BY source_item_id, collected_at DESC, snapshot_id DESC
"""


def sample_local(per_cue: int = 3) -> dict:
    if not 1 <= per_cue <= 20:
        raise ValueError("per-cue limit must be between 1 and 20")
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
        ],
    }
    documents: dict[str, dict] = {}
    with connect(runtime_url()) as conn:
        conn.autocommit = True
        conn.execute("SET default_transaction_read_only = on")
        for source in (*COMMERCE, YOUTUBE):
            base = YOUTUBE_BASE if source == YOUTUBE else COMMERCE_BASE
            params = () if source == YOUTUBE else (source,)
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
