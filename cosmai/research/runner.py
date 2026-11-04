"""Bounded adaptive decision/execution loop. Calls run outside database transactions."""

from __future__ import annotations

import json
import time
from functools import partial
from uuid import uuid4

from . import literature
from .fulltext import CaptureFailure, context_result
from .participation import integrate, required_decisions
from .state import (
    ACTIONS,
    READ_ACTIONS,
    Proposal,
    evidence_spans,
    resolve_argument_citations,
    validate_citations,
)
from .store import Conflict, StaleRevision, Store
from .tools import EvidenceSource, bounded_read
from .workers import ProviderFailure


def apply_result(state: dict, result: dict):
    for evidence in result.get("evidence", []):
        old = state["evidence"].get(evidence["id"])
        if old and old != evidence:
            raise ValueError("immutable evidence changed")
        state["evidence"][evidence["id"]] = evidence


def transition(state: dict, action: str, args: dict, revision: int):
    if action == "integrate_feedback":
        integrate(state, args)
    elif action == "assess":
        for hypothesis in args["hypotheses"]:
            validate_citations(hypothesis["support"] + hypothesis["counterevidence"], state["evidence"])
            if hypothesis["status"] == "supported" and not hypothesis["support"]:
                raise ValueError("supported hypothesis needs evidence")
            state["hypotheses"][hypothesis["id"]] = hypothesis
        pending_dependencies = {
            t for d in state["discussions"].values() if d["status"] == "pending" for t in d["dependent_tasks"]
        }
        for task in args["tasks"]:
            if task["id"] in pending_dependencies and task["status"] != "blocked":
                raise ValueError("pending participant decision still blocks task")
            state["tasks"][task["id"]] = task
        state["summary"] = args["summary"]
        state["summary_goal_revision"] = state["goal_revision"]
        state["criteria_history"].append(
            {
                "version": len(state["criteria_history"]) + 1,
                "criteria": args["criteria"],
                "reason": args["criteria_reason"],
                "effect": args["effect_on_prior_assessments"],
                "goal_revision": state["goal_revision"],
            }
        )
    elif action == "discuss":
        validate_citations(args["evidence"], state["evidence"])
        if args["id"] in state["discussions"]:
            raise ValueError("discussion ID already exists")
        if args["authorized_default"] is not None:
            raise ValueError("first-slice discussions have no implicit authorized default")
        if any(t not in state["tasks"] for t in args["dependent_tasks"]):
            raise ValueError("discussion dependency must identify a known task")
        state["discussions"][args["id"]] = {
            **args,
            "goal_revision": state["goal_revision"],
            "state_revision": revision,
            "status": "pending",
        }
        for task in args["dependent_tasks"]:
            state["tasks"][task]["status"] = "blocked"
        if (
            args["dependent_tasks"]
            and state["tasks"]
            and not any(
                t["status"] == "ready"
                and all(state["tasks"].get(dep, {}).get("status") == "done" for dep in t["dependencies"])
                for t in state["tasks"].values()
            )
        ):
            state.update(
                status="blocked",
                outcome="participant_input_required",
                reason="All remaining agenda tasks depend on participant input",
            )
    elif action == "finish":
        if args["outcome"] == "participant_input_required" and not any(
            d["status"] == "pending" for d in state["discussions"].values()
        ):
            raise ValueError("participant stop requires a prepared pending discussion")
        validate_citations(args["evidence"], state["evidence"])
        if args["outcome"] == "findings" and not args["evidence"]:
            raise ValueError("findings need traceable source evidence")
        if required_decisions(state):
            if args["outcome"] != "participant_input_required":
                raise ValueError("pending decisions must survive in the stopping reason")
        state["summary_goal_revision"] = state["goal_revision"]
        state["findings"].append({**args, "goal_revision": state["goal_revision"]})
        state.update(
            summary=args["summary"],
            outcome=args["outcome"],
            reason=args["next_validation"],
            status="blocked" if args["outcome"] == "participant_input_required" else "finished",
        )
    elif action == "pause":
        state.update(status="paused", outcome="participant_input_required", reason=args["reason"])
    elif action == "request_capability":
        state["development_requests"].append(args)


def apply_transition(state, result, *, action, revision):
    transition(state, action, result["arguments"], revision)


def validation_error(exc):
    from pydantic import ValidationError

    if isinstance(exc, ValidationError):
        return [
            {"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]}
            for e in exc.errors(include_input=False, include_context=False, include_url=False)
        ][:12]
    # Only application validation messages are exposed, not provider/connection exception text.
    return str(exc)[:1000] if isinstance(exc, ValueError) else type(exc).__name__


class Runner:
    def __init__(self, store: Store, source: EvidenceSource, worker):
        self.store, self.source, self.worker = store, source, worker

    def context(self, sid: str, request_id: str) -> dict:
        row = self.store.get(sid)
        state = row["state"]
        actions = self.store.actions(sid)
        # All observations are retained in storage; an oversized context stops explicitly, not by forgetting.
        context = {
            "request_id": request_id,
            "session_id": sid,
            "observed_revision": row["revision"],
            "goal_revision": state["goal_revision"],
            "state": state,
            "available_actions": self.source.schemas(),
            "evidence_spans": {key: evidence_spans(e) for key, e in state["evidence"].items()},
            "evidence_index": [
                {"id": e["id"], "source": e["source"], "locator": e["locator"]}
                for e in self.source.evidence.values()
            ],
            "observations": [
                {
                    "execution_id": a["id"],
                    "action": a["kind"],
                    "status": a["status"],
                    "result": context_result(a["kind"], a["result"], state["evidence"]),
                    "input_revision": a["observed_revision"],
                }
                for a in actions
                if a["kind"] != "manager"
            ],
            "recent_provider_errors": [
                a["result"] for a in actions if a["kind"] == "manager" and a["status"] == "failed"
            ][-3:],
            "remaining_resources": {
                key: state["mission"]["limits"][key] - state["usage"][key]
                for key in ("manager_calls", "tool_calls")
            },
        }
        context["remaining_resources"]["seconds"] = self.store.remaining_seconds(sid)
        if self.source.literature_limit:
            context["remaining_resources"]["literature_actions"] = max(
                0, self.source.literature_limit - sum(a["kind"] in literature.ACTIONS for a in actions)
            )
        resources = context["remaining_resources"]
        pressure = []
        if min(resources["manager_calls"], resources["tool_calls"]) <= 3:
            pressure.append("calls")
        if resources["seconds"] <= 2 * state["mission"]["limits"]["call_seconds"]:
            pressure.append("time")
        if resources.get("literature_actions", 12) <= 2:
            pressure.append("literature")
        if len(json.dumps(context)) >= 110_000:
            pressure.append("context")
            # Preserve the complete evidence, including contrary passages. Reserve headroom by
            # closing further acquisition, not by selecting which science the worker may see.
            context["available_actions"] = {
                key: value for key, value in context["available_actions"].items() if key not in READ_ACTIONS
            }
        resources.update(
            pressure=pressure,
            synthesis_reserve_actions=2,
            context_limit_characters=150_000,
            context_characters=0,
            context_remaining_characters=0,
        )
        # Include the accounting fields themselves in the measurement (integer widths settle).
        for _ in range(4):
            size = len(json.dumps(context))
            resources.update(context_characters=size, context_remaining_characters=150_000 - size)
        if len(json.dumps(context)) > 150_000:
            raise ValueError("context_limit; narrow evidence pack or implement indexed context retrieval")
        return context

    def run(self, sid: str, *, steps: int | None = None) -> dict:
        if steps is not None and steps < 1:
            raise ValueError("steps must be positive")
        token = self.store.acquire(sid, self.worker.identity)
        completed = 0
        try:
            self.store.bind_source(sid, token, self.source.identity)
            while steps is None or completed < steps:
                row = self.store.get(sid)
                if row["state"]["status"] != "running":
                    break
                request_id = str(uuid4())
                try:
                    context = self.context(sid, request_id)
                except ValueError as exc:
                    self.store.stop(sid, token, "resource_limit", str(exc))
                    break
                try:
                    reserved = self.store.reserve(
                        sid, token, request_id, "manager", context["observed_revision"], context
                    )
                except StaleRevision:
                    continue
                if reserved is None:
                    break
                timeout = min(
                    context["state"]["mission"]["limits"]["call_seconds"], self.store.remaining_seconds(sid)
                )
                try:
                    value, usage = self.worker.decide(context, max(0.01, timeout))
                except ProviderFailure as exc:
                    self.store.complete(
                        sid,
                        token,
                        reserved["id"],
                        {"error": str(exc)},
                        exc.usage,
                        failure=True,
                        uncertain=exc.uncertain,
                    )
                    continue
                try:
                    proposal = Proposal.model_validate(value)
                    if (
                        proposal.request_id != request_id
                        or proposal.observed_revision != context["observed_revision"]
                        or proposal.goal_revision != context["goal_revision"]
                    ):
                        raise ValueError("proposal identity or revision does not match request")
                    if proposal.action not in context["available_actions"]:
                        raise ValueError("capability unavailable; record a development request")
                    args = ACTIONS[proposal.action].model_validate(proposal.arguments).model_dump()
                    if proposal.action not in READ_ACTIONS:
                        args = resolve_argument_citations(args, context["state"]["evidence"])
                    validate_citations(
                        [e.model_dump() for e in proposal.evidence], context["state"]["evidence"]
                    )
                    if any(f.get("status") == "pending" for f in context["state"]["feedback"]):
                        if proposal.action != "integrate_feedback":
                            raise ValueError("interpret pending feedback with integrate_feedback first")
                    if proposal.task_id:
                        task = context["state"]["tasks"].get(proposal.task_id)
                        if not task or task["status"] != "ready":
                            raise ValueError("task unavailable or blocked")
                        if any(
                            context["state"]["tasks"].get(dep, {}).get("status") != "done"
                            for dep in task["dependencies"]
                        ):
                            raise ValueError("task dependency incomplete")
                    elif required_decisions(context["state"]):
                        if proposal.action in READ_ACTIONS:
                            raise ValueError(
                                "while a decision is pending, identify an independent ready task"
                            )
                except ValueError as exc:
                    self.store.complete(
                        sid,
                        token,
                        reserved["id"],
                        {
                            "error": "invalid_decision",
                            "details": validation_error(exc),
                            "hint": "Correct the named fields using available_actions and evidence_spans",
                        },
                        usage,
                        failure=True,
                    )
                    continue
                if not self.store.complete(sid, token, reserved["id"], proposal.model_dump(), usage):
                    continue
                try:
                    action = self.store.reserve(
                        sid,
                        token,
                        request_id,
                        proposal.action,
                        proposal.observed_revision,
                        proposal.model_dump(),
                    )
                except StaleRevision:
                    continue
                if action is None:
                    break
                if action["status"] != "reserved":
                    continue
                started = time.monotonic()
                try:
                    if proposal.action in READ_ACTIONS:
                        result = bounded_read(
                            self.source,
                            proposal.action,
                            args,
                            self.store.actions(sid),
                            min(
                                context["state"]["mission"]["limits"]["call_seconds"],
                                self.store.remaining_seconds(sid),
                            ),
                        )
                        apply = apply_result
                    else:
                        result = {
                            "arguments": args,
                            "changed": proposal.changed,
                            "rationale": proposal.rationale,
                        }

                        apply = partial(
                            apply_transition, action=proposal.action, revision=proposal.observed_revision
                        )
                    self.store.complete(
                        sid,
                        token,
                        action["id"],
                        result,
                        {"elapsed_seconds": time.monotonic() - started},
                        apply=apply,
                    )
                except Conflict:
                    raise
                except Exception as exc:
                    self.store.complete(
                        sid,
                        token,
                        action["id"],
                        {
                            "error_class": type(exc).__name__,
                            "meaning": "action failed; not negative evidence",
                            "details": validation_error(exc),
                            **(exc.artifact if isinstance(exc, CaptureFailure) else {}),
                        },
                        {"elapsed_seconds": time.monotonic() - started},
                        failure=True,
                    )
                completed += 1
            if self.store.get(sid)["state"]["status"] == "running":
                self.store.stop(
                    sid, token, "step_limit", "Invocation step bound reached; resume uses saved state"
                )
        finally:
            self.store.release(sid, token)
        return self.store.get(sid)
