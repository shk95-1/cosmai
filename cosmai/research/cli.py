"""Create, inspect, continue and participate in durable research sessions."""

from __future__ import annotations

import json
import os
from pathlib import Path


def add_parser(subparsers):
    research = subparsers.add_parser("research", help="Manage durable research and prepared participation.")
    actions = research.add_subparsers(dest="research_action", required=True)
    for name in ("create", "run", "status", "feedback", "pause", "reconcile"):
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
        if name == "run":
            parser.add_argument("--worker", choices=["codex", "claude"], default="codex")
            source = parser.add_mutually_exclusive_group(required=True)
            source.add_argument("--evidence-file")
            source.add_argument("--evidence-url-env")
            parser.add_argument("--source-profile", required=True, help="Non-secret source identity.")
            parser.add_argument("--steps", type=int)
        if name == "status":
            parser.add_argument("--json", action="store_true")
        if name == "feedback":
            parser.add_argument("--text", required=True)
            parser.add_argument("--discussion")
        if name == "reconcile":
            parser.add_argument("--execution", required=True)
            parser.add_argument("--disposition", required=True)


def status_text(row: dict) -> str:
    state = row["state"]
    lines = [
        f"Session {row['id']} | {state['status']} | rev {row['revision']} / goal {state['goal_revision']}",
        state["mission"]["objective"],
        f"Findings (goal {state.get('summary_goal_revision', 'none')}): {state['summary']}",
        f"Outcome: {state['outcome'] or 'pending'} — {state['reason']}",
        f"Resources: {json.dumps(state['usage'], sort_keys=True)}",
        f"Limits: {json.dumps(state['mission']['limits'], sort_keys=True)}",
    ]
    for feedback in state["feedback"]:
        interpretation = feedback.get("interpretation")
        if interpretation:
            lines.append("Participant response: " + interpretation["response"])
    for hypothesis in state["hypotheses"].values():
        lines.append(f"{hypothesis['id']} ({hypothesis['status']}): {hypothesis['problem']}")
        lines.append("Uncertainty: " + "; ".join(hypothesis["uncertainties"]))
    for finding in state["findings"]:
        lines.append("Uncertainty: " + "; ".join(finding["uncertainties"]))
        lines.append("Next validation: " + finding["next_validation"])
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
        if action == "run":
            source = (
                EvidenceSource.from_file(args.evidence_file, args.source_profile)
                if args.evidence_file
                else EvidenceSource(
                    profile=args.source_profile, database_url=os.environ.get(args.evidence_url_env)
                )
            )
            worker = CodexWorker() if args.worker == "codex" else ClaudeWorker()
            Runner(store, source, worker).run(args.session_id, steps=args.steps)
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
