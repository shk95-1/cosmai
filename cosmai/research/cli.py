"""Create, inspect, continue and participate in durable research sessions."""

from __future__ import annotations

import json
import os
from pathlib import Path


def add_parser(subparsers):
    research = subparsers.add_parser("research", help="Manage durable research and prepared participation.")
    actions = research.add_subparsers(dest="research_action", required=True)
    for name in ("create", "run", "participate", "status", "feedback", "pause", "reconcile"):
        parser = actions.add_parser(name)
        parser.add_argument(
            "--session-url-env",
            default="COSMAI_RESEARCH_SESSION_URL",
            help="Environment key containing the explicit session database URL.",
        )
        if name == "create":
            parser.add_argument("--mission-file", required=True)
        else:
            parser.add_argument("session_id")
        if name in ("run", "participate"):
            parser.add_argument("--worker", choices=["codex", "claude"], default="codex")
            source = parser.add_mutually_exclusive_group(required=True)
            source.add_argument("--evidence-file")
            source.add_argument("--evidence-url-env")
            parser.add_argument("--source-profile", required=True, help="Non-secret source identity.")
            if name == "run":
                parser.add_argument("--steps", type=int)
        if name == "status":
            parser.add_argument("--json", action="store_true")
        if name == "feedback":
            parser.add_argument("--text", required=True)
            parser.add_argument("--discussion")
        if name == "reconcile":
            parser.add_argument("--execution", required=True)
            parser.add_argument("--disposition", required=True)


def status_text(row: dict, *, current_only: bool = False) -> str:
    state = row["state"]
    lines = [
        f"Session {row['id']} | {state['status']} | rev {row['revision']} / goal {state['goal_revision']}",
        state["mission"]["objective"],
        f"Findings (goal {state.get('summary_goal_revision', 'none')}): {state['summary']}",
        f"Outcome: {state['outcome'] or 'pending'} — {state['reason']}",
        f"Resources: {json.dumps(state['usage'], sort_keys=True)}",
        f"Limits: {json.dumps(state['mission']['limits'], sort_keys=True)}",
    ]
    feedback_items = [f for f in state["feedback"] if f.get("interpretation")]
    for feedback in feedback_items[-1:] if current_only else feedback_items:
        interpretation = feedback.get("interpretation")
        if interpretation:
            lines.append("Participant response: " + interpretation["response"])
    for hypothesis in state["hypotheses"].values():
        lines.append(f"{hypothesis['id']} ({hypothesis['status']}): {hypothesis['problem']}")
        lines.append("Uncertainty: " + "; ".join(hypothesis["uncertainties"]))
    for finding in state["findings"][-1:] if current_only else state["findings"]:
        lines.append("Uncertainty: " + "; ".join(finding["uncertainties"]))
        lines.append("Next validation: " + finding["next_validation"])
        for citation in finding["evidence"]:
            evidence = state["evidence"][citation["evidence_id"]]
            lines.append(f"Source {evidence['locator']}: {citation['passage']}")
    for discussion in state["discussions"].values():
        if discussion["status"] != "pending":
            continue
        lines.extend(
            [
                f"Discussion {discussion['id']} (goal {discussion['goal_revision']}, "
                f"state {discussion['state_revision']}): {discussion['question']}",
                discussion["why_now"],
                discussion["finding"],
                "Uncertainty: " + discussion["uncertainty"],
                "Options: " + " | ".join(discussion["options"]),
                "Recommendation: " + discussion["recommendation"],
                "Strongest objection: " + discussion["strongest_objection"],
                "Your contribution: " + discussion["contribution"],
                "Continues while waiting: " + discussion["independent_work"],
            ]
        )
        for citation in discussion["evidence"]:
            evidence = state["evidence"][citation["evidence_id"]]
            lines.append(f"Source {evidence['locator']}: {citation['passage']}")
    for evidence in state["evidence"].values():
        lines.append(f"Evidence {evidence['id']}: {evidence['locator']} ({evidence['sha256'][:12]})")
    return "\n".join(lines)


def participation_citations(state):
    citations = []
    if state["findings"]:
        citations.extend(state["findings"][-1]["evidence"])
    else:
        for hypothesis in state["hypotheses"].values():
            citations.extend(hypothesis["support"] + hypothesis["counterevidence"])
    for discussion in state["discussions"].values():
        if discussion["status"] == "pending":
            citations.extend(discussion["evidence"])
    # An interrupted investigation can have inspected passages without prepared findings.
    if not citations:
        citations = [{"evidence_id": key, "passage": e["passage"]} for key, e in state["evidence"].items()]
    return citations


def participation_sources(row):
    state = row["state"]
    lines = ["Original source passages from the displayed research state:"]
    seen = set()
    for citation in participation_citations(state):
        key = (citation["evidence_id"], citation["passage"])
        if key in seen:
            continue
        seen.add(key)
        evidence = state["evidence"][citation["evidence_id"]]
        lines.append(f"Source {evidence['locator']}: {citation['passage']}")
    if not seen:
        lines.append("No inspected source passages are available yet.")
    return "\n".join(lines)


def participation_text(row):
    """Present substantive durable content without duplicate quotations or diagnostic bookkeeping."""
    state = row["state"]
    lines = ["Research update", state["mission"]["objective"]]
    stale = state.get("summary_goal_revision") not in (None, state["goal_revision"])
    label = "Findings from an earlier objective" if stale else "Findings"
    lines.append(f"{label}: {state['summary']}")
    finding = state["findings"][-1] if state["findings"] else None
    outcome_labels = {
        "findings": "Qualified findings prepared.",
        "evidence_limit": "Evidence limit reached; the investigation remains qualified.",
        "participant_input_required": "Participant input is needed for dependent work.",
    }
    if state["outcome"] in outcome_labels:
        lines.append(outcome_labels[state["outcome"]])
    if state["outcome"] not in (None, "findings", "evidence_limit", "participant_input_required"):
        lines.append("Research stopped: " + state["reason"])
    elif state["reason"] and (not finding or state["reason"] != finding["next_validation"]):
        lines.append("Current position: " + state["reason"])
    for feedback in [f for f in state["feedback"] if f.get("interpretation")][-1:]:
        lines.append("Response to your contribution: " + feedback["interpretation"]["response"])
    if finding:
        if finding["goal_revision"] != state["goal_revision"]:
            lines.append("The following uncertainty and proposed validation belong to the earlier objective.")
        lines.extend(
            [
                "Uncertainty: " + "; ".join(finding["uncertainties"]),
                "Next validation: " + finding["next_validation"],
            ]
        )
    else:
        for hypothesis in state["hypotheses"].values():
            lines.extend(
                [
                    "Hypothesis: " + hypothesis["problem"],
                    "Uncertainty: " + "; ".join(hypothesis["uncertainties"]),
                ]
            )
    for discussion in state["discussions"].values():
        if discussion["status"] != "pending":
            continue
        lines.extend(
            [
                "Your input: " + discussion["question"],
                "Why now: " + discussion["why_now"],
                "Evidence so far: " + discussion["finding"],
                "Uncertainty: " + discussion["uncertainty"],
                "Options: " + " | ".join(discussion["options"]),
                "Recommendation: " + discussion["recommendation"],
                "Strongest objection: " + discussion["strongest_objection"],
                "Your contribution: " + discussion["contribution"],
                "Continues while waiting: " + discussion["independent_work"],
            ]
        )
    locators = dict.fromkeys(
        state["evidence"][c["evidence_id"]]["locator"] for c in participation_citations(state)
    )
    if locators:
        lines.append(
            "Sources (original passages: /sources during participation, or research status; "
            "no research calls used):"
        )
        lines.extend(locators)
    usage, limits = state["usage"], state["mission"]["limits"]
    lines.append(
        f"Resources: decisions {usage['manager_calls']}/{limits['manager_calls']}; "
        f"actions {usage['tool_calls']}/{limits['tool_calls']}; "
        f"failures {usage['failures']}/{limits['failures']}; uncertain {usage['uncertain']}"
    )
    lines.append(
        f"Deadline: {row['deadline'] or 'starts on first execution'}; "
        f"original duration {limits['seconds']}s, per-call limit {limits['call_seconds']}s. "
        "Waiting uses the original duration; limits are not renewed."
    )
    return "\n".join(lines)


def continuation_blocker(row):
    state = row["state"]
    if row["lease_until"] and row["lease_until"] > row["clock"]:
        return "another runner is active"
    if state["usage"]["uncertain"]:
        return "an interrupted execution needs reconciliation"
    if row["deadline"] and row["deadline"] <= row["clock"]:
        return "the original deadline has expired"
    for key in ("manager_calls", "tool_calls", "failures"):
        if state["usage"][key] >= state["mission"]["limits"][key]:
            return f"the original {key} allowance is exhausted"
    if state["outcome"] == "resource_limit":
        return state["reason"]
    return None


def participate(runner, sid, *, read=input, write=print):
    """Use the displayed revision for natural input and continue inside the original envelope."""
    from .store import Conflict, StaleRevision

    store = runner.store
    row = store.get(sid)
    if row["state"].get("source") not in (None, runner.source.identity):
        raise Conflict("evidence source changed")
    if not continuation_blocker(row) and (
        row["state"]["status"] in ("ready", "running") or row["state"]["outcome"] == "step_limit"
    ):
        write("Research is continuing within the original resource limits.")
        row = runner.run(sid)
    while True:
        write(participation_text(row))
        blocker = continuation_blocker(row)
        if blocker:
            write(f"Continuation unavailable: {blocker}. No resources were renewed.")
            return row
        try:
            while True:
                text = read(
                    "Your question, idea or response (/sources inspects sources; empty input exits): "
                )
                if text.strip() != "/sources":
                    break
                write(participation_sources(row))
        except (EOFError, KeyboardInterrupt):
            text = ""
        if not text.strip():
            write("Participation ended. No answer or consent was supplied.")
            return store.get(sid)
        try:
            store.feedback(sid, text, observed_revision=row["revision"])
        except StaleRevision:
            write(
                "Research changed while you were reading; your text was not submitted. "
                "Review and re-enter it."
            )
            row = store.get(sid)
            continue
        except ValueError:
            write("Please enter 1–8000 characters; your text was not submitted.")
            continue
        write("Contribution saved. Continuing within the original resource limits.")
        row = store.get(sid)
        if continuation_blocker(row):
            continue
        try:
            row = runner.run(sid)
        except Conflict:
            write(
                "Contribution remains saved; another execution or changed source prevents this continuation."
            )
            return store.get(sid)


def run(args) -> int:
    from .runner import Runner
    from .state import Mission
    from .store import Conflict, Store
    from .tools import EvidenceSource
    from .workers import ClaudeWorker, CodexWorker

    try:
        store = Store(os.environ.get(args.session_url_env, ""))
        action = args.research_action
        if action == "create":
            mission = Mission.model_validate_json(Path(args.mission_file).read_text())
            print(store.create(mission))
            return 0
        if action in ("run", "participate"):
            source = (
                EvidenceSource.from_file(args.evidence_file, args.source_profile)
                if args.evidence_file
                else EvidenceSource(
                    profile=args.source_profile, database_url=os.environ.get(args.evidence_url_env)
                )
            )
            worker = CodexWorker() if args.worker == "codex" else ClaudeWorker()
            runner = Runner(store, source, worker)
            if action == "participate":
                participate(runner, args.session_id)
                return 0
            runner.run(args.session_id, steps=args.steps)
        elif action == "feedback":
            store.feedback(args.session_id, args.text, args.discussion)
        elif action == "pause":
            store.pause(args.session_id)
        elif action == "reconcile":
            store.reconcile(args.session_id, args.execution, args.disposition)
        row = store.get(args.session_id)
        if action == "status" and args.json:
            print(json.dumps(row, default=str, ensure_ascii=False, indent=2))
        else:
            print(status_text(row))
        return 0
    except (ValueError, Conflict) as exc:
        # Validation errors may contain source or credential input; only the category is public.
        print(f"Research command refused ({type(exc).__name__}); check configuration and session state.")
        return 2
    except Exception as exc:
        print(f"Research command failed ({type(exc).__name__}); existing session records are preserved.")
        return 2
