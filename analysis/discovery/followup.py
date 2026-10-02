"""Replay a bounded alternative comparison against the unchanged value ranking."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from analysis.discovery.alternatives import market_reason
from analysis.discovery.core import fingerprint
from analysis.discovery.value import screen


def compare(sample: dict, review: dict, brief: dict, value_review: dict, assessment: dict) -> dict:
    """Keep the selected value hypothesis and every favorable original observation visible."""
    value = screen(sample, review, brief, value_review)
    if not value["selected"] or assessment.get("candidate") != value["selected"]:
        raise ValueError("compare only the unchanged automatically selected value hypothesis")
    candidate = next(
        c for c in value["original_selection"]["candidates"] if c["candidate"] == value["selected"]
    )
    reason = market_reason(sample, candidate, assessment)
    docs = {d["doc_id"]: d for d in sample["documents"]}
    contexts = assessment.get("context_alignment", [])
    support = {r["doc_id"]: r["observation"] for r in candidate["support"]}
    if len(contexts) != len(support) or {r["doc_id"] for r in contexts} != set(support):
        raise ValueError("align every original supporting context exactly once")
    for row in contexts:
        if any(row.get(k) != support[row["doc_id"]][k] for k in ("context", "behavior", "trade_off")):
            raise ValueError("context, behavior and compromise must preserve original observations")
        if not all(row.get(k) for k in ("desired_benefit", "original_strength", "usage_alignment")):
            raise ValueError("state desired use, exposure uncertainty and instruction alignment")
    positives = assessment.get("reviewed_counterevidence", [])
    counters = {r["doc_id"]: r["observation"] for r in candidate["counterevidence"]}
    if len(positives) != len(counters) or {r["doc_id"] for r in positives} != set(counters):
        raise ValueError("preserve every favorable original observation exactly once")
    for row in positives:
        if (
            row.get("product_key") != docs[row["doc_id"]].get("product_key")
            or row.get("span") != counters[row["doc_id"]]["span"]
            or not row.get("interpretation")
        ):
            raise ValueError("counterevidence needs its original listing, full span and interpretation")
    exclusions = assessment.get("reviewed_exclusion_doc_ids", [])
    if len(exclusions) != len(candidate["related_exclusions"]) or set(exclusions) != {
        r["doc_id"] for r in candidate["related_exclusions"]
    }:
        raise ValueError("preserve all original related exclusions")
    sources = assessment.get("sources", [])
    budget = assessment.get("budget", {})
    if (
        budget.get("inspected_page_limit") != 8
        or type(budget.get("inspected_pages")) is not int
        or not 0 < budget["inspected_pages"] <= 8
        or len(sources) != budget["inspected_pages"]
        or len({s["id"] for s in sources}) != len(sources)
        or len({s["url"] for s in sources}) != len(sources)
        or not budget.get("counting")
        or not budget.get("stop")
    ):
        raise ValueError("account for every inspected or denied page within the eight-page cap")
    for source in sources:
        date.fromisoformat(source["retrieved_on"])
        if (
            urlsplit(source["url"]).scheme != "https"
            or source.get("access") not in {"available", "partial", "unavailable"}
            or not source.get("method")
            or not source.get("summary")
        ):
            raise ValueError("sources need explicit access, provenance and readable-content limits")
    source_ids = {s["id"] for s in sources}
    for alternative in assessment["alternatives"]:
        if not alternative.get("source_ids") or not set(alternative["source_ids"]) <= source_ids:
            raise ValueError("alternative references must reach the inspected source ledger")
    # The existing product validator owns adequacy/gap claims; an unknown routine
    # remains an agent-recorded possibility, never a second path to technical release.
    routines = assessment.get("routine_alternatives", [])
    requirements = {r["id"] for r in assessment["requirements"]}
    if not routines:
        raise ValueError("ordinary accepted usage and clearer instructions must be considered")
    for routine in routines:
        comparisons = routine.get("comparisons", [])
        if (
            not routine.get("name")
            or len(comparisons) != len(requirements)
            or {c["requirement"] for c in comparisons} != requirements
            or any(c.get("status") != "unknown" or not c.get("reason") for c in comparisons)
        ):
            raise ValueError("unverified routine alternatives must cover all contexts as unknown")
    remaining = [
        c["candidate"]
        for c in value["candidates"]
        if c["status"] == "research_value_hypothesis" and c["candidate"] != value["selected"]
    ]
    basis = assessment["decision"].get("basis")
    disposition = {
        "insufficient_common_requirement": "hold_common_requirement",
        "existing_alternative": "existing_acceptable_alternative",
        "insufficient_alternative_evidence": "insufficient_alternative_evidence",
    }.get(basis, "supported_residual_requirement")
    inputs = {"value_run_id": value["run_id"], "assessment": fingerprint(assessment)}
    return {
        "version": "alternative-followup-v1",
        "run_id": fingerprint(inputs),
        "inputs": inputs,
        "candidate": value["selected"],
        "disposition": disposition,
        "market_reason": reason,
        "release": "none",
        "next_candidate": remaining[0] if reason and remaining else None,
        "comparison": deepcopy(assessment),
        "original_selection": value["original_selection"],
        "value_screen": value,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("sample", "review", "brief", "value-review", "assessment", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    paths = [args.sample, args.review, args.brief, args.value_review, args.assessment]
    if args.output.resolve() in {p.resolve() for p in paths}:
        raise ValueError("output cannot overwrite evidence inputs")
    report = compare(*(json.loads(p.read_text()) for p in paths))
    args.output.touch(mode=0o600)
    args.output.chmod(0o600)
    args.output.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ("run_id", "disposition", "next_candidate", "release")}))


if __name__ == "__main__":
    main()
