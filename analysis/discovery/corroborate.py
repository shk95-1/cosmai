"""Equal-budget, requirement-led replication of every observed seed, read-only.

The queries are agent research inputs anchored to the frozen seed. Matching terms never
establish a common requirement: every returned document still needs semantic review.
"""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import UTC, datetime

from psycopg.rows import dict_row
from psycopg.sql import SQL

from analysis.discovery.core import fingerprint, freeze, select, validate_snapshot
from analysis.discovery.sample import COMMERCE, COMMERCE_BASE, YOUTUBE, YOUTUBE_BASE
from analysis.retrieval.corpus import review_doc_id
from db.runtime import connect, runtime_url


def validate_queries(sample: dict, review: dict, queries: dict, per_query: int) -> None:
    validate_snapshot(sample)
    if not 1 <= per_query <= 3:
        raise ValueError("corroboration quota must be between one and three")
    if review["snapshot_sha256"] != sample["snapshot_sha256"]:
        raise ValueError("seed review targets another snapshot")
    select(sample, review)  # require the complete grounded seed review before touching the DB
    if queries.get("snapshot_sha256") != sample["snapshot_sha256"] or not queries.get("reviewer"):
        raise ValueError("queries need a reviewer and the frozen seed snapshot")
    observed = {o["candidate"] for o in review["observations"] if o["disposition"] == "observed"}
    if set(queries["groups"]) != observed or not 1 <= len(observed) <= 25:
        raise ValueError("query every observed seed group under the same bounded quota")
    docs = {d["doc_id"]: d for d in sample["documents"]}
    for key, query in queries["groups"].items():
        support = {
            o["doc_id"]
            for o in review["observations"]
            if o["disposition"] == "observed" and o["candidate"] == key
        }
        if not query.get("reason") or not query.get("anchors"):
            raise ValueError("requirement query needs its reasoning and source anchors")
        for anchor in query["anchors"]:
            if (
                anchor["doc_id"] not in support
                or not anchor.get("span")
                or anchor["span"] not in docs[anchor["doc_id"]]["text"]
            ):
                raise ValueError("query anchor is not an observed seed span")
        terms = query["all_of"]
        if not 1 <= len(terms) <= 4 or any(
            not alternatives or len(alternatives) > 8 for alternatives in terms
        ):
            raise ValueError("query needs one to four bounded literal term groups")
        if any(not isinstance(term, str) or not 1 <= len(term) <= 40 for group in terms for term in group):
            raise ValueError("literal query terms must have 1–40 characters")


def corroborate_local(sample: dict, review: dict, queries: dict, per_query: int = 3) -> dict:
    """Keep seed texts unchanged; exclude seed IDs and preserve all query/source outcomes."""
    validate_queries(sample, review, queries, per_query)
    documents = {d["doc_id"]: deepcopy(d) for d in sample["documents"]}
    manifest = deepcopy(sample["manifest"])
    manifest["corroboration"] = {
        "started_at": datetime.now(UTC).isoformat(),
        "seed_snapshot_sha256": sample["snapshot_sha256"],
        "queries_sha256": fingerprint(queries),
        "queries": queries,
        "per_group_source": per_query,
        "ordering": "stable MD5 source-ID order; original seed IDs excluded",
        "coverage": [],
        "failures": [],
        "changed_documents": [],
        "paid_calls": 0,
        "gpu_hours": 0,
        "limitations": [
            "queries are agent-derived hypotheses, not independent labels",
            "literal matches require full semantic and independence review",
            "original source/cue sampling bias persists; no worldwide coverage",
            "autocommit reads are not one transaction-wide source snapshot",
        ],
    }
    ledger = manifest["corroboration"]
    with connect(runtime_url()) as conn:
        conn.autocommit = True
        conn.execute("SET default_transaction_read_only = on")
        for source in (*COMMERCE, YOUTUBE):
            base = YOUTUBE_BASE if source == YOUTUBE else COMMERCE_BASE
            params = () if source == YOUTUBE else (source,)
            original_ids = [d["source_item_id"] for d in sample["documents"] if d["source"] == source]
            for key, query in sorted(queries["groups"].items()):
                # Terms are literals, never interpolated SQL or caller-supplied regex code.
                patterns = ["(" + "|".join(re.escape(t) for t in group) + ")" for group in query["all_of"]]
                where = SQL(" AND ").join(
                    [SQL("text ~* %s")] * len(patterns) + [SQL("NOT (source_item_id = ANY(%s))")]
                )
                try:
                    with conn.cursor(row_factory=dict_row) as cur:
                        cur.execute(
                            SQL("SELECT count(*) AS matching FROM ({base}) evidence WHERE {where}").format(
                                base=SQL(base), where=where
                            ),
                            (*params, *patterns, original_ids),
                        )
                        matching = (cur.fetchone() or {})["matching"]
                        cur.execute(
                            SQL(
                                "SELECT * FROM ({base}) evidence WHERE {where} "
                                "ORDER BY md5(source_item_id), source_item_id LIMIT %s"
                            ).format(base=SQL(base), where=where),
                            (*params, *patterns, original_ids, per_query),
                        )
                        rows = cur.fetchall()
                        ledger["coverage"].append(
                            {"source": source, "candidate": key, "matching": matching, "returned": len(rows)}
                        )
                        for row in rows:
                            doc_id = (
                                f"{YOUTUBE}:{row['source_item_id']}"
                                if source == YOUTUBE
                                else review_doc_id(source, row["source_item_id"])
                            )
                            if doc_id not in documents:
                                row.update(doc_id=doc_id, source=source, cues=[], corroboration_queries=[])
                                documents[doc_id] = row
                            elif row["text"] != documents[doc_id]["text"]:
                                ledger["changed_documents"].append({"doc_id": doc_id, "candidate": key})
                                continue
                            documents[doc_id]["corroboration_queries"].append(key)
                except Exception as exc:
                    ledger["failures"].append(
                        {"source": source, "candidate": key, "error_class": type(exc).__name__}
                    )
    ledger["new_documents"] = len(documents) - len(sample["documents"])
    ledger["finished_at"] = datetime.now(UTC).isoformat()
    return freeze(manifest, list(documents.values()))
