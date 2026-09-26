"""Replay a source-bound, agent-authored development case without a model or database."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from analysis.discovery.core import fingerprint, select
from analysis.discovery.technical import validate_technical


def _seconds(start: str, finish: str) -> float:
    return (datetime.fromisoformat(finish) - datetime.fromisoformat(start)).total_seconds()


def build(sample: dict, review: dict, technical: dict, spec: dict) -> dict:
    """Validate references and replay selection; prose quality still needs real review."""
    candidate = validate_technical(sample, review, technical)
    if (
        spec.get("version") != "brief-spec-v1"
        or spec.get("candidate") != candidate["candidate"]
        or spec.get("snapshot_sha256") != sample["snapshot_sha256"]
        or spec.get("review_sha256") != fingerprint(review)
        or spec.get("technical_sha256") != fingerprint(technical)
        or spec["objective"].get("decision_url") != technical["release"]["decision_url"]
    ):
        raise ValueError("case specification targets different evidence or decision")
    docs = {r["doc_id"] for r in candidate["support"]}
    claims = {c["id"] for c in technical["claims"]}
    target = spec["target"]
    if (
        not target.get("statement")
        or target.get("basis") != "agent_inference"
        or not target.get("support_doc_ids")
        or not set(target["support_doc_ids"]) <= docs
    ):
        raise ValueError("target must reference observed supporting consumers")
    for section in ("conditions", "tradeoffs", "failure_modes"):
        if not spec.get(section):
            raise ValueError("case needs conditions, trade-offs and failure modes")
        for item in spec[section]:
            if set(item) != {"statement", "basis", "support_doc_ids", "technical_claim_ids"}:
                raise ValueError("case text is a referenced hypothesis, not measurement results")
            if (
                not item["statement"]
                or item["basis"] != "design_hypothesis"
                or not set(item["support_doc_ids"]) <= docs
                or not set(item["technical_claim_ids"]) <= claims
                or not (item["support_doc_ids"] or item["technical_claim_ids"])
            ):
                raise ValueError("case statement needs applicable consumer or technical references")
    if not all(spec.get(k) for k in ("title", "falsifiers", "boundaries")) or not spec["objective"].get(
        "statement"
    ):
        raise ValueError("case needs an objective, falsifiers and limits")
    result = select(sample, review)
    prior = deepcopy(review)
    prior["market_reviews"].pop(candidate["candidate"])
    manifest = sample["manifest"]
    corroboration = manifest.get("corroboration", {})
    ledgers = [manifest, corroboration, technical["budget"]]
    counters = ("paid_calls", "gpu_hours", "production_writes", "purchases", "outreach")
    budget = {k: sum(ledger.get(k, 0) for ledger in ledgers) for k in counters}
    if any(ledger.get(k, 0) != 0 for ledger in ledgers for k in counters):
        raise ValueError("this offline zero-budget case cannot certify a paid/GPU run")
    contrast = candidate["market_review"]["coverage"].get("contrastive_pass", {})
    stages = [
        {
            "stage": "uniform_source_capture",
            "started_at": manifest["started_at"],
            "finished_at": manifest["finished_at"],
            "elapsed_seconds": _seconds(manifest["started_at"], manifest["finished_at"]),
            "failures": manifest["failures"],
        },
    ]
    if corroboration:
        stages.append(
            {
                "stage": "equal_quota_corroboration",
                "started_at": corroboration["started_at"],
                "finished_at": corroboration["finished_at"],
                "elapsed_seconds": _seconds(corroboration["started_at"], corroboration["finished_at"]),
                "new_documents": corroboration["new_documents"],
                "group_source_queries": len(corroboration["coverage"]),
                "failures": corroboration["failures"],
                "changed_documents": corroboration["changed_documents"],
            }
        )
    run = {
        "mode": "agent_mediated_workflow_with_offline_replay",
        "selection_before_candidate_comparison": select(sample, prior)["selected"],
        "selection_with_recorded_comparisons": result["selected"],
        "release": deepcopy(technical["release"]),
        "documents": len(sample["documents"]),
        "observation_dispositions": dict(Counter(o["disposition"] for o in review["observations"])),
        "candidates": [
            {
                k: c[k]
                for k in (
                    "candidate",
                    "qualified",
                    "rejection_reasons",
                    "rank",
                    "source_local_users",
                    "dates",
                )
            }
            for c in result["candidates"]
        ],
        "agent_interventions": deepcopy(review["interventions"]),
        "agent_research_seconds": None,
        "timing_limit": "Only capture and current offline replay are timed. Prior semantic review, public "
        "research and conversation time were not instrumented; "
        "no end-to-end speed or time saving is claimed.",
        "stages": stages,
        "contrastive_access": [
            {k: s.get(k) for k in ("id", "url", "access", "provenance")} for s in contrast.get("sources", [])
        ],
        "technical_access": [
            {k: s[k] for k in ("id", "url", "access", "limitations")} for s in technical["sources"]
        ],
        "budget": {
            **budget,
            "paid_currency_spend": 0,
            "scope": "Case data/public-source work and offline generation only; provided agent subscription, "
            "host resources and implementation/test time are not priced.",
        },
        "source_limits": deepcopy(manifest["bias"]),
    }
    inputs = {
        "sample": sample["snapshot_sha256"],
        "review": fingerprint(review),
        "technical": fingerprint(technical),
        "spec": fingerprint(spec),
    }
    return {
        "version": "development-brief-v1",
        "run_id": fingerprint(inputs),
        "inputs": inputs,
        "status": "conditional_brief_for_user_evaluation",
        "acceptance": "pending_separate_user_evaluation",
        "spec": deepcopy(spec),
        "case": deepcopy(candidate),
        "technical": deepcopy(technical),
        "run": run,
    }


def render(report: dict) -> str:
    """Make the complete case inspectable without hiding counterevidence or failed gates."""
    spec, candidate, technical, run = (report[k] for k in ("spec", "case", "technical", "run"))
    lines = [
        f"# {spec['title']}",
        "",
        f"Status: `{report['status']}`. User acceptance: pending (#327).",
        "",
        spec["objective"]["statement"],
        "",
        "## Target and observed workaround",
        "",
        spec["target"]["statement"],
        "",
        f"{candidate['source_local_users']} source-local identities across {len(candidate['sources'])} sites "
        f"and {len(candidate['dates'])} dates support this research segment. "
        "Globally distinct people and market size are unverified. "
        "Original exact supporting spans are retained in the JSON evidence record.",
        "",
    ]
    for support in candidate["support"]:
        o = support["observation"]
        lines.extend([f"### {support['doc_id']}", "", f"Date: {support['published_at']}.", ""])
        for field in ("context", "requirement", "behavior", "burden", "trade_off"):
            lines.append(f"- {field}: {o[field]['summary']}")
        lines.append("")
        if support["url"]:
            lines.extend([f"[Source]({support['url']})", ""])
        else:
            lines.extend(
                [
                    "Individual review URL unavailable; the frozen source/review coordinates identify the "
                    "evidence. Product listing URLs below are not review permalinks.",
                    "",
                ]
            )
    lines.extend(
        [
            "## Selection and user decision",
            "",
            f"Original automatic wording: {candidate['need']}",
            "",
            "This stronger no-wiping framing is frozen research history. The current objective above allows "
            "adjustment and is not evidence that the original stricter comparison passed.",
            "",
            "Before this candidate's comparison, automatic selection: "
            f"`{run['selection_before_candidate_comparison']}`.",
            "With recorded comparisons, provisional automatic selection: "
            f"`{run['selection_with_recorded_comparisons']}` "
            "(its alternatives/technical gates have not passed).",
            "",
            f"Actual release route: `{technical['release']['route']}`; "
            f"[recorded user judgment]({technical['release']['decision_url']}) "
            f"at {technical['release']['recorded_at']}.",
            "",
            technical["release"]["rationale"],
            "",
            f"Original lip gate remains `{candidate['market_review']['decision']['status']}` / "
            f"`{candidate['market_review']['decision']['basis']}`; "
            f"automatic qualified flag: `{candidate['qualified']}`.",
            "",
        ]
    )
    for key, title in (
        ("conditions", "Essential conditions"),
        ("tradeoffs", "Trade-offs"),
        ("failure_modes", "Likely failure modes"),
    ):
        lines.extend([f"## {title}", ""])
        for item in spec[key]:
            refs = item["support_doc_ids"] + item["technical_claim_ids"]
            lines.append(f"- Hypothesis: {item['statement']} References: {', '.join(refs)}.")
        lines.append("")
    market = candidate["market_review"]
    lines.extend(
        [
            "## Examined alternatives",
            "",
            "These statuses refer to the frozen original comparison. They do not establish whether "
            "an ordinary routine meets the current lower-effort objective. Listing identity does not "
            "prove shade, package or cross-site equivalence.",
            "",
        ]
    )
    for alternative in market["alternatives"]:
        identity = alternative["identity"]
        lines.extend(
            [
                f"### {alternative['name']}",
                "",
                f"Identity: {identity['source']} / {identity['product_id']} / {identity['level']}.",
                "",
                f"[Listing]({alternative['source']['url']})",
                "",
            ]
        )
        for comparison in alternative["comparisons"]:
            lines.append(f"- {comparison['requirement']}: **{comparison['status']}**. {comparison['reason']}")
        lines.append("")
    contrast = market["coverage"].get("contrastive_pass", {})
    for source in contrast.get("sources", []):
        lines.extend(
            [
                f"- [{source['id']}]({source['url']}) ({source['kind']}; {source['access']}; "
                f"retrieved {source['retrieved_on']}): {source['summary']} {source['provenance']}"
            ]
        )
    lines.extend(
        [
            "",
            "## Established ingredient, formulation and manufacturing directions",
            "",
            "Start with an established suitable base formula and stock wiper/tip matching, holding formula "
            "constant in package comparisons. Commercial silicone film-former families are a possible "
            "existing ingredient direction, not an adopted recipe. No synthesis of the paper's novel resin "
            "or reproduction of patented geometry is proposed.",
            "",
        ]
    )
    sources = {s["id"]: s for s in technical["sources"]}
    for claim in technical["claims"]:
        refs = ", ".join(f"[{key}]({sources[key]['url']})" for key in claim["source_ids"])
        lines.extend(
            [
                f"### {claim['id']} ({claim['basis']})",
                "",
                claim["statement"],
                "",
                f"Material/component: {claim['material_or_component']}. Conditions: {claim['conditions']}",
                "",
                f"Case applicability: {claim['applicability']} Limits: {claim['limits']}",
                "",
                refs,
                "",
            ]
        )
    lines.extend(
        [
            "## Proposed measurements and controls",
            "",
            "All tests are proposed_not_run. Equipment, numerical acceptance targets and an available "
            "laboratory/partner are not assumed.",
            "",
        ]
    )
    for test in technical["proposed_tests"]:
        lines.extend(
            [
                f"### {test['id']} ({test['state']})",
                "",
                test["objective"],
                "",
                f"Control: {test['control']}",
                "",
                f"Measure: {test['measure']}",
                "",
                test["limitations"],
                "",
            ]
        )
    lines.extend(["## Unknowns and stopping conditions", ""])
    lines.extend(
        f"- {s}" for s in technical["disposition"]["unknowns"] + spec["falsifiers"] + spec["boundaries"]
    )
    lines.extend(["", "## Technical source applicability", ""])
    for source in technical["sources"]:
        lines.extend(
            [
                f"- [{source['id']}: {source['title']}]({source['url']}) — {source['publisher']}; "
                f"{source['kind']}; {source['access']}; published {source.get('published_on') or 'undated'}; "
                f"retrieved {source['retrieved_on']}; {source['locator']}. "
                f"{source['conditions']} {source['limitations']}"
            ]
        )
    lines.extend(
        [
            "",
            "## Reproducible run and rejected candidates",
            "",
            f"Run ID: `{report['run_id']}`.",
            "",
            f"{run['documents']} frozen documents; "
            f"dispositions: {json.dumps(run['observation_dispositions'], sort_keys=True)}.",
            "",
            "The original foundation lead was rejected for incompatible common requirements; its evidence "
            "and decision remain in this replay. Other provisional groups remain research candidates, "
            "not released development cases.",
            "",
        ]
    )
    for c in run["candidates"]:
        lines.append(
            f"- {c['candidate']}: qualified={c['qualified']}; {c['source_local_users']} source-local users; "
            f"reasons: {'; '.join(c['rejection_reasons']) or 'none at this preliminary stage'}."
        )
    lines.extend(["", "Agent interventions:", ""])
    for intervention in run["agent_interventions"]:
        lines.append(f"- {intervention['detail'] if isinstance(intervention, dict) else intervention}")
    lines.extend(
        [
            "- Human intervention: the explicit provisional user judgment released the held lip case. "
            "A separate finished-brief usefulness evaluation remains pending.",
            "",
            run["timing_limit"],
            "",
        ]
    )
    for stage in run["stages"]:
        lines.append(
            f"- {stage['stage']}: {stage['started_at']} to {stage['finished_at']}; "
            f"{stage['elapsed_seconds']:.3f} seconds; recorded source failures: {len(stage['failures'])}."
        )
    execution = report.get("execution")
    if execution:
        lines.append(
            f"- Current offline replay/render: {execution['started_at']} to {execution['finished_at']}; "
            f"{execution['elapsed_seconds']:.6f} seconds. This is not whole-research duration."
        )
    lines.extend(
        [
            "",
            "Source capture: zero failures in the recorded uniform/corroboration passes. Partial/failed "
            "public-source access is preserved in the alternative and technical ledgers above; inaccessible "
            "evidence supplies no decisive claim.",
            "",
            f"Budget: {json.dumps(run['budget'], sort_keys=True)}",
            "",
            "Input digests (JSON record includes the same values):",
            "",
        ]
    )
    lines.extend(f"- {k}: `{v}`" for k, v in report["inputs"].items())
    lines.extend(["", "Source limits:", ""])
    lines.extend(f"- {s}" for s in run["source_limits"])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("sample", "review", "technical", "case", "output"):
        parser.add_argument(f"--{flag}", type=Path, required=True)
    args = parser.parse_args()
    inputs = [args.sample, args.review, args.technical, args.case]
    outputs = [args.output.with_suffix(suffix) for suffix in (".json", ".md")]
    if {p.resolve() for p in inputs} & {p.resolve() for p in outputs}:
        parser.error("output must not overwrite a source input")
    started = datetime.now(UTC).isoformat()
    clock = perf_counter()
    report = build(*(json.loads(p.read_text()) for p in inputs))
    render(report)
    report["execution"] = {
        "mode": "offline_replay_of_agent_reviewed_inputs",
        "started_at": started,
        "finished_at": datetime.now(UTC).isoformat(),
        "elapsed_seconds": perf_counter() - clock,
    }
    for output, text in zip(
        outputs, (json.dumps(report, ensure_ascii=False, indent=2) + "\n", render(report)), strict=True
    ):
        with output.open("w", encoding="utf-8") as f:
            output.chmod(0o600)
            f.write(text)
    print(json.dumps({k: report[k] for k in ("run_id", "status", "acceptance")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
