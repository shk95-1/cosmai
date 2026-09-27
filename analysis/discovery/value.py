"""Screen source-bound value hypotheses without turning inconvenience into demand.

The semantic assessment is an agent input, not a severity classifier. Validation
checks its provenance and scope; qualitative interpretations still require review.
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from analysis.discovery.core import fingerprint, select

CRITERIA = [
    "executed effort or recovery rather than an imagined inconvenience",
    "desired result still compromised in actual use",
    "explicitly retained benefits and rejected routines or products",
    "plausible incremental benefit, with actual alternative adequacy still unverified",
]
STATUSES = {"research_value_hypothesis", "hold_common_requirement", "stopped_delivered_direction"}
KINDS = {"executed_action", "compromised_result", "retained_benefit", "rejected_choice"}


def screen(sample: dict, review: dict, brief: dict, assessment: dict) -> dict:
    """Preserve original gates and screen every corroborated current hypothesis fairly."""
    original = select(sample, review)
    inputs = {"sample": sample["snapshot_sha256"], "review": fingerprint(review)}
    if (
        brief.get("version") != "development-brief-v1"
        or any(brief.get("inputs", {}).get(k) != v for k, v in inputs.items())
        or brief.get("run_id") != fingerprint(brief.get("inputs"))
        or brief["inputs"].get("spec") != fingerprint(brief.get("spec"))
        or brief["inputs"].get("technical") != fingerprint(brief.get("technical"))
    ):
        raise ValueError("delivered brief targets different evidence")
    inputs["brief"] = fingerprint(brief)
    if (
        assessment.get("version") != "value-review-v1"
        or assessment.get("inputs") != inputs
        or not assessment.get("reviewer")
        or assessment.get("criteria") != CRITERIA
    ):
        raise ValueError("value review needs unchanged evidence and common criteria")
    evaluation = assessment.get("evaluation", {})
    evaluated = brief["spec"]["candidate"]
    decision = urlsplit(evaluation.get("decision_url", ""))
    if (
        evaluation.get("candidate") != evaluated
        or evaluation.get("brief_run_id") != brief["run_id"]
        or evaluation.get("brief_sha256") != inputs["brief"]
        or evaluation.get("direction") != brief["spec"]["title"]
        or evaluation.get("decision") != "stop_delivered_direction"
        or evaluation.get("reason") != "insufficient_problem_importance_or_new_product_value"
        or decision.scheme != "https"
        or not decision.netloc
        or not decision.fragment
    ):
        raise ValueError("stop evaluation must identify the delivered direction and decision")
    recorded = datetime.fromisoformat(evaluation.get("recorded_at", "").replace("Z", "+00:00"))
    if recorded.tzinfo is None:
        raise ValueError("evaluation timestamp needs a timezone")
    # Only recover the already delivered candidate for calibration. Other existing
    # market rejections (including incoherent foundation requirements) remain gates.
    calibration_review = deepcopy(review)
    calibration_review.get("market_reviews", {}).pop(evaluated, None)
    calibration = select(sample, calibration_review)
    eligible = {c["candidate"]: c for c in calibration["candidates"] if c["qualified"]}
    assessments = assessment.get("candidates", [])
    if (
        len(assessments) != len(eligible)
        or {c.get("candidate") for c in assessments} != set(eligible)
        or evaluated not in eligible
    ):
        raise ValueError("review every corroborated candidate exactly once")
    docs = {d["doc_id"]: d for d in sample["documents"]}
    screened = []
    for item in assessments:
        candidate = eligible[item["candidate"]]
        support = {r["doc_id"] for r in candidate["support"]}
        counters = {r["doc_id"] for r in candidate["counterevidence"]}
        if (
            item.get("support_sha256") != fingerprint(candidate["support"])
            or set(item.get("reviewed_support_doc_ids", [])) != support
            or set(item.get("reviewed_counterevidence_doc_ids", [])) != counters
            or item.get("status") not in STATUSES
            or (item["status"] == "stopped_delivered_direction") != (item["candidate"] == evaluated)
        ):
            raise ValueError("value status needs complete unchanged support and counterevidence")
        evidence = item.get("observations", [])
        if not evidence or any(
            e.get("basis") != "consumer_report"
            or e.get("kind") not in KINDS
            or e.get("doc_id") not in support | counters
            or not e.get("span")
            or e["span"] not in docs[e["doc_id"]]["text"]
            or not e.get("summary")
            for e in evidence
        ):
            raise ValueError("value observations require applicable exact consumer spans")
        hypothesis = item.get("value_hypothesis", {})
        if (
            hypothesis.get("basis") != "agent_inference"
            or not all(
                hypothesis.get(k) for k in ("beneficiary", "incremental_gain", "uncertainties", "falsifier")
            )
            or not item.get("counterevidence_assessment")
            or not item.get("disposition_reason")
        ):
            raise ValueError("value hypothesis must separate inference, uncertainty and falsification")
        if item["status"] == "research_value_hypothesis" and (
            not {"executed_action", "compromised_result"}
            <= {e["kind"] for e in evidence if e["doc_id"] in support}
            or hypothesis.get("alternative_adequacy") != "unverified"
        ):
            raise ValueError(
                "research hypothesis requires observed action/result and unverified alternatives"
            )
        screened.append({**deepcopy(item), "original_rank": candidate["rank"]})
    # Use the existing ranking, not a subjective severity score or a hand-picked category.
    screened.sort(key=lambda c: (tuple(-x for x in c["original_rank"]), c["candidate"]))
    next_candidates = [c for c in screened if c["status"] == "research_value_hypothesis"]
    inputs["assessment"] = fingerprint(assessment)
    return {
        "version": "value-screen-v1",
        "run_id": fingerprint(inputs),
        "inputs": inputs,
        "status": "selected_for_alternative_research" if next_candidates else "no_qualified_value_hypothesis",
        "selected": next_candidates[0]["candidate"] if next_candidates else None,
        "release": "bounded_alternative_research_only" if next_candidates else "none",
        "evaluation": deepcopy(evaluation),
        "criteria": CRITERIA,
        "candidates": screened,
        "original_selection": original,
        "limits": [
            "Agent interpretations and references are auditable, not independently validated severity.",
            "Consumer symptom reports are not diagnoses, causation or treatment guidance.",
            "Existing alternatives may fully solve each remaining problem; new-product value is unverified.",
            "No market size, willingness to pay, technical readiness or usefulness acceptance is inferred.",
            "Stop applies to this delivered direction; another direction needs its own case and evaluation.",
            "No release of technical briefing, portal stages, purchases, paid/GPU work or production writes.",
        ],
    }


def render(report: dict) -> str:
    lines = [
        "# Development value screening",
        "",
        f"Status: `{report['status']}`. Next: `{report['selected']}`.",
        "",
        "This is an agent-reviewed research disposition. New-product value remains unverified.",
        "",
        f"[Actual user stop decision]({report['evaluation']['decision_url']}): "
        "insufficient problem importance or new-product value.",
        "",
    ]
    for c in report["candidates"]:
        lines += [f"## {c['candidate']}", "", f"Disposition: `{c['status']}`. {c['disposition_reason']}", ""]
        for e in c["observations"]:
            lines += [
                f"- {e['kind']} / `{e['doc_id']}`: {e['summary']}",
                f"  Exact consumer span: {e['span']}",
            ]
        h = c["value_hypothesis"]
        lines += [
            "",
            f"Agent hypothesis: {h['incremental_gain']}",
            "",
            f"Counterevidence: {c['counterevidence_assessment']}",
            "",
            f"Uncertainty: {h['uncertainties']}",
            "",
            f"Falsifier: {h['falsifier']}",
            "",
        ]
    lines += [
        "## Preserved original selection",
        "",
        f"All {len(report['original_selection']['candidates'])} groups, support, ranks and exclusions "
        "remain in the JSON report. Original selected candidate: "
        f"`{report['original_selection']['selected']}`.",
        "",
        "## Limits",
        "",
    ]
    lines += [f"- {limit}" for limit in report["limits"]]
    lines += ["", "## Replay identity", "", f"Run ID: `{report['run_id']}`.", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("sample", "review", "brief", "assessment", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    inputs = [args.sample, args.review, args.brief, args.assessment]
    outputs = [args.output.with_suffix(".json"), args.output.with_suffix(".md")]
    if {p.resolve() for p in inputs} & {p.resolve() for p in outputs}:
        raise ValueError("output cannot overwrite evidence inputs")
    report = screen(*(json.loads(p.read_text()) for p in inputs))
    for path, content in zip(
        outputs, [json.dumps(report, ensure_ascii=True, indent=2) + "\n", render(report)], strict=True
    ):
        path.touch(mode=0o600)
        path.chmod(0o600)
        path.write_text(content)
    print(json.dumps({k: report[k] for k in ("run_id", "status", "selected", "release")}))


if __name__ == "__main__":
    main()
