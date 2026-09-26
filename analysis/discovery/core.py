"""Ground semantic observations in a frozen source sample, then select transparently.

An agent supplies semantic reviews, never a chosen winner. A review is an auditable input,
not an independent label or a claim that this heuristic forecasts demand.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from analysis.retrieval.normalize import normalize_text

VERSION = "workaround-v1"
DISPOSITIONS = {"observed", "wish", "promotion", "ambiguous", "satisfied", "off_topic"}
HASH = re.compile(r"(?:[a-f0-9]{16}|[a-f0-9]{24}|[a-f0-9]{64})\Z")


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def freeze(manifest: dict, documents: list[dict]) -> dict:
    """Bind full normalized text and source coordinates, not a mutable live query."""
    documents = sorted(documents, key=lambda d: d["doc_id"])
    if len({d["doc_id"] for d in documents}) != len(documents):
        raise ValueError("duplicate document ID")
    for d in documents:
        d["text"] = normalize_text(d["text"])
        d["text_sha256"] = fingerprint(d["text"])
        if d.get("author_hash") and not HASH.fullmatch(d["author_hash"]):
            raise ValueError("author identifier must be a stored hash")
    sample = {"version": VERSION, "manifest": manifest, "documents": documents}
    sample["snapshot_sha256"] = fingerprint(sample)
    return sample


def validate_snapshot(sample: dict) -> None:
    if sample.get("version") != VERSION:
        raise ValueError("unsupported discovery version")
    if sample.get("snapshot_sha256") != fingerprint(
        {k: v for k, v in sample.items() if k != "snapshot_sha256"}
    ):
        raise ValueError("snapshot was changed")
    ids = set()
    for d in sample["documents"]:
        if d["doc_id"] in ids or d["text_sha256"] != fingerprint(d["text"]):
            raise ValueError("duplicate ID or invalid text digest")
        ids.add(d["doc_id"])
        if d.get("author_hash") and not HASH.fullmatch(d["author_hash"]):
            raise ValueError("invalid author hash")


def _utc_date(value: str) -> str:
    when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if when.tzinfo is None:
        raise ValueError("evidence timestamp needs an explicit timezone")
    return when.astimezone(UTC).date().isoformat()


def select(sample: dict, review: dict) -> dict:
    """Rank open-vocabulary groups; reject ungrounded, copied and unattributed support.

    Two identifiable users are a corroboration safeguard for this pilot, not a popularity
    threshold. A tiny persistent group can qualify; missing authors never count as new users.
    No candidate is said to be absent from the market: #324 still compares alternatives.
    """
    validate_snapshot(sample)
    if review.get("snapshot_sha256") != sample["snapshot_sha256"]:
        raise ValueError("review targets a different snapshot")
    if not review.get("reviewer") or not isinstance(review.get("interventions"), list):
        raise ValueError("reviewer and intervention ledger required")
    docs = {d["doc_id"]: d for d in sample["documents"]}
    observations = review["observations"]
    if len(observations) != len(docs) or {o["doc_id"] for o in observations} != set(docs):
        raise ValueError("review every sampled document exactly once")
    specs = review["candidates"]
    groups: dict[str, list[dict]] = defaultdict(list)
    rejected = []
    seen_text: set[str] = set()
    for o in sorted(observations, key=lambda item: item["doc_id"]):
        d = docs[o["doc_id"]]
        disposition = o["disposition"]
        if disposition not in DISPOSITIONS or not o.get("reason"):
            raise ValueError("disposition and reason required")
        candidate = o.get("candidate")
        if candidate is not None and candidate not in specs:
            raise ValueError("undeclared candidate")
        # Every claimed positive field has its own exact supporting span. This prevents an
        # isolated behavior keyword from standing in for motivation or an unresolved trade-off.
        if disposition == "observed":
            for field in ("context", "requirement", "behavior", "burden", "trade_off"):
                claim = o.get(field, {})
                if not claim.get("summary") or not claim.get("span") or claim["span"] not in d["text"]:
                    raise ValueError(f"ungrounded {field}: {d['doc_id']}")
            if not candidate or type(o.get("recurring")) is not bool:
                raise ValueError("observed behavior needs candidate and explicit recurrence")
            if o["recurring"] and (not o.get("recurrence_span") or o["recurrence_span"] not in d["text"]):
                raise ValueError("recurrence requires its own supporting span")
        elif not o.get("span") or o["span"] not in d["text"]:
            raise ValueError("rejected/satisfied observation still needs an exact supporting span")
        reason = None
        if d["text_sha256"] in seen_text:
            reason = "normalized duplicate; no independent support"
        elif disposition != "observed":
            reason = f"{disposition}: {o['reason']}"
        elif not d.get("author_hash"):
            reason = "author unavailable; independence unestablished"
        seen_text.add(d["text_sha256"])
        record = {
            "doc_id": d["doc_id"],
            "candidate": candidate,
            "reason": reason,
            "source": d["source"],
            "url": d.get("url"),
            "published_at": d.get("published_at"),
            "text_sha256": d["text_sha256"],
            "observation": o,
        }
        if reason:
            rejected.append(record)
        else:
            if candidate is None:
                raise ValueError("observed candidate missing")
            groups[candidate].append(record)
    candidates = []
    for key, spec in sorted(specs.items()):
        if set(spec) != {"need", "limitations", "technical", "feasibility"}:
            raise ValueError("candidate spec has unexpected fields")
        if not spec.get("need") or not spec.get("limitations"):
            raise ValueError("candidate need and limitations required")
        technical = spec["technical"]
        feasibility = spec["feasibility"]
        if technical["status"] not in {"unassessed", "available", "contradictory"}:
            raise ValueError("invalid technical status")
        if technical["status"] == "available" and not technical.get("references"):
            raise ValueError("available technical evidence needs references")
        if feasibility["status"] not in {"unassessed", "existing_process_hypothesis", "incompatible"}:
            raise ValueError("invalid feasibility status")
        if not technical.get("reason") or not feasibility.get("reason"):
            raise ValueError("technical and feasibility uncertainty must be explicit")
        support = groups[key]
        # One author at one site can write many reviews; that remains one user. Different-site
        # identities cannot be reconciled: report this instead of claiming global unique users.
        users = {(docs[r["doc_id"]]["source"], docs[r["doc_id"]]["author_hash"]) for r in support}
        dates = sorted({_utc_date(r["published_at"]) for r in support if r["published_at"]})
        reasons = []
        if len(users) < 2:
            reasons.append("fewer than two attributable source-local users")
        recurring = any(r["observation"]["recurring"] for r in support)
        if not recurring and len(dates) < 2:
            reasons.append("repeatability unestablished")
        if technical["status"] == "contradictory" or feasibility["status"] == "incompatible":
            reasons.append("preliminary technical/feasibility conflict")
        # Observed effort, specific context/requirement and trade-off were required per record.
        # Corroboration is capped so popularity cannot overwhelm evidence quality.
        score = [
            int(recurring),
            min(len(users), 3),
            int(len(dates) > 1),
            int(technical["status"] == "available"),
            int(feasibility["status"] == "existing_process_hypothesis"),
        ]
        candidates.append(
            {
                "candidate": key,
                **spec,
                "qualified": not reasons,
                "rejection_reasons": reasons,
                "source_local_users": len(users),
                "sources": sorted({r["source"] for r in support}),
                "dates": dates,
                "rank": score,
                "support": support,
                "related_exclusions": [r for r in rejected if r["candidate"] == key],
                "counterevidence": [
                    r
                    for r in rejected
                    if r["candidate"] == key and r["observation"]["disposition"] == "satisfied"
                ],
            }
        )
    qualified = sorted(
        (c for c in candidates if c["qualified"]),
        key=lambda c: (tuple(-x for x in c["rank"]), c["candidate"]),
    )
    winner = qualified[0]["candidate"] if qualified else None
    for c in candidates:
        if c["qualified"] and c["candidate"] != winner:
            c["rejection_reasons"] = ["qualified but lower deterministic rank; retained for later review"]
    return {
        "version": VERSION,
        "snapshot_sha256": sample["snapshot_sha256"],
        "review_sha256": fingerprint(review),
        "status": "selected" if winner else "no_qualified_candidate",
        "selected": winner,
        "criteria": [
            "anchored actual effort and unresolved trade-off",
            "recurrence",
            "capped corroboration",
            "different dates",
            "technical evidence",
            "existing-process hypothesis",
        ],
        "candidates": candidates,
        "rejected_observations": rejected,
        "manifest": sample["manifest"],
        "reviewer": review["reviewer"],
        "interventions": review["interventions"],
        "limitations": [
            "bounded cue sample is not a market survey or absence test",
            "source-local users are not verified globally distinct people",
            "agent semantic grouping is a replayable research input, not independent validation",
            "selection is provisional until alternatives and technical review",
        ],
    }
