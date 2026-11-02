"""Research execution boundaries and integrated recovery, using explicit isolated sources."""

from pathlib import Path
from typing import LiteralString, cast

import pytest
from psycopg.sql import SQL

from cosmai.research.runner import Runner, transition
from cosmai.research.state import Evidence, Limits, Mission
from cosmai.research.store import Conflict, Store
from cosmai.research.tools import EvidenceSource, bounded_read
from cosmai.research.workers import ProviderFailure


def test_mission_rejects_unknown_authority_and_paid_budget():
    from cosmai.research.state import Mission

    with pytest.raises(ValueError):
        Mission.model_validate(
            {"objective": "Find a useful opportunity", "scope": "controlled", "paid_budget": 1}
        )


def test_unspecified_source_never_uses_runtime_database():
    from cosmai.research.tools import EvidenceSource

    with pytest.raises(ValueError, match="explicit"):
        EvidenceSource(profile="test")


@pytest.fixture
def source():
    return EvidenceSource(
        profile="controlled-pack-v1",
        bundle={
            "evidence": [
                Evidence(
                    id="trial",
                    source="controlled experiment",
                    locator="fixture:trial",
                    captured_at="2026-11-02",
                    passage=(
                        "In this fictional pilot, the pump delivered 0.2 g with lower variation than the jar."
                    ),
                    limitations=["Synthetic fixture; not cosmetics efficacy evidence"],
                ).frozen(),
                Evidence(
                    id="limit",
                    source="controlled counterexample",
                    locator="fixture:limit",
                    captured_at="2026-11-02",
                    passage=(
                        "No sensitive-skin tolerance or customer demand was measured. "
                        "The pump leaked in transit."
                    ),
                    limitations=["Synthetic fixture; not a live product claim"],
                ).frozen(),
            ]
        },
    )


@pytest.fixture
def store(database_url_for_tests, _schema_name):
    store = Store(database_url_for_tests)
    ddl = Path("contracts/ddl/needs/031_research_session.sql").read_text()
    with store.connection() as conn:
        conn.execute(SQL(cast(LiteralString, ddl.replace("needs.", f'"{_schema_name}".'))))
    return store


def proposal(context, action, arguments, task_id=None):
    return {
        "request_id": context["request_id"],
        "observed_revision": context["observed_revision"],
        "goal_revision": context["goal_revision"],
        "action": action,
        "arguments": arguments,
        "rationale": "Investigate the discriminating evidence",
        "question": "What does the source support?",
        "expected_result": "A traceable qualified observation",
        "changed": "Prior observation identifies a gap",
        "evidence": [],
        "task_id": task_id,
    }


class ScriptedWorker:
    identity = "controlled-fixture/not-live"

    def __init__(self, *steps):
        self.steps = list(steps)
        self.contexts = []

    def decide(self, context, timeout):
        self.contexts.append(context)
        step = self.steps.pop(0)
        if callable(step):
            return step(context), {"fixture": True}
        return proposal(context, *step), {"fixture": True}


def finding():
    return {
        "summary": "Pump dosing is promising for validation; no tolerance or demand conclusion.",
        "evidence": [
            {"evidence_id": "trial", "passage": "the pump delivered 0.2 g"},
            {"evidence_id": "limit", "passage": "The pump leaked in transit."},
        ],
        "uncertainties": ["Sensitive-skin suitability unknown", "Transit leakage unresolved"],
        "next_validation": "Measure shipment leakage before a qualified product decision",
        "outcome": "findings",
    }


def agenda():
    return {
        "summary": "Two independent questions",
        "hypotheses": [],
        "tasks": [
            {
                "id": "choice",
                "question": "Launch speed or differentiation?",
                "information_gain": "Resolve an actual priority",
                "method": "participant",
                "status": "ready",
            },
            {
                "id": "independent",
                "question": "What is unmeasured?",
                "information_gain": "Bound claims",
                "method": "read evidence",
                "status": "ready",
            },
        ],
        "criteria": "Applicable evidence with explicit limits",
        "criteria_reason": "Baseline criterion",
        "effect_on_prior_assessments": "None yet",
    }


def discussion():
    return {
        "id": "strategy",
        "question": "Favor launch speed or differentiation?",
        "why_now": "The comparison depends on the business objective",
        "finding": "Two routes remain",
        "evidence": [],
        "uncertainty": "Commercial priority unknown",
        "options": ["Speed: fewer new features", "Differentiation: more validation"],
        "recommendation": "Validate a distinct benefit",
        "strongest_objection": "Longer lead time",
        "contribution": "Choose the business priority",
        "dependent_tasks": ["choice"],
        "independent_work": "Inspect already available counterevidence",
        "authorized_default": None,
    }


class TestDurableResearch:
    pytestmark = pytest.mark.postgres

    def test_successive_actions_feedback_and_fresh_worker_resume(self, store, source):
        sid = store.create(Mission(objective="Investigate dosing", scope="controlled evidence only"))
        first = ScriptedWorker(("read_evidence", {"evidence_id": "trial"}))
        row = Runner(store, source, first).run(sid, steps=1)
        assert row["state"]["outcome"] == "step_limit"
        fresh = ScriptedWorker(("read_evidence", {"evidence_id": "limit"}), ("finish", finding()))
        row = Runner(Store(store.url), source, fresh).run(sid)
        assert row["state"]["status"] == "finished"
        assert fresh.contexts[0]["state"]["evidence"]["trial"]["passage"]
        assert fresh.contexts[1]["observations"][-1]["result"]["evidence"][0]["id"] == "limit"
        assert len(row["state"]["findings"]) == 1
        store.feedback(sid, "Prioritize transit reliability instead of dosing accuracy")
        row = store.get(sid)
        assert row["state"]["goal_revision"] == 1
        assert row["state"]["status"] == "ready"
        assert row["state"]["findings"][0]["goal_revision"] == 0
        assert row["state"]["usage"]["manager_calls"] == 3

    def test_participation_blocks_only_dependencies_and_stale_response_is_preserved(self, store, source):
        sid = store.create(Mission(objective="Evaluate priorities", scope="controlled"))
        worker = ScriptedWorker(
            ("assess", agenda()),
            ("discuss", discussion()),
            lambda c: proposal(c, "read_evidence", {"evidence_id": "limit"}, "independent"),
        )
        row = Runner(store, source, worker).run(sid, steps=3)
        assert row["state"]["tasks"]["choice"]["status"] == "blocked"
        assert "limit" in row["state"]["evidence"]
        assert row["state"]["discussions"]["strategy"]["status"] == "pending"
        store.feedback(sid, "Reliability is now the only priority")
        store.feedback(sid, "Choose speed", "strategy")
        state = store.get(sid)["state"]
        assert state["goal_revision"] == 1
        assert not state["feedback"][-1]["applied"]
        assert state["tasks"]["independent"]["status"] == "superseded"

    def test_feedback_during_manager_rejects_old_decision(self, store, source):
        sid = store.create(Mission(objective="Test revision", scope="controlled"))

        def interrupted(context):
            store.feedback(sid, "Prefer reliability")
            return proposal(context, "pause", {"reason": "Old plan must not apply"})

        worker = ScriptedWorker(interrupted, ("pause", {"reason": "New goal considered"}))
        row = Runner(store, source, worker).run(sid)
        assert row["state"]["reason"] == "New goal considered"
        assert any(a["status"] == "stale" for a in store.actions(sid))
        assert len([a for a in store.actions(sid) if a["kind"] == "pause"]) == 1

    def test_feedback_during_tool_preserves_old_observation_without_applying(self, store, source):
        sid = store.create(Mission(objective="Test in-flight feedback", scope="controlled"))
        original = source.execute

        def interrupted(action, args, records):
            store.feedback(sid, "Changed priority during source read")
            return original(action, args, records)

        source.execute = interrupted
        worker = ScriptedWorker(
            ("read_evidence", {"evidence_id": "trial"}), ("pause", {"reason": "Reconsidered old observation"})
        )
        row = Runner(store, source, worker).run(sid)
        assert row["state"]["evidence"] == {}
        stale = [a for a in store.actions(sid) if a["kind"] == "read_evidence"][0]
        assert stale["status"] == "stale" and stale["result"]["evidence"][0]["id"] == "trial"
        assert worker.contexts[-1]["observations"][0]["status"] == "stale"

    def test_duplicate_delivery_and_stale_fence(self, store):
        sid = store.create(Mission(objective="Recover safely", scope="controlled"))
        token = store.acquire(sid, "first")
        revision = store.get(sid)["revision"]
        a = store.reserve(sid, token, "request-1", "search", revision, {"query": "pump"})
        duplicate = store.reserve(sid, token, "request-1", "search", revision, {"query": "pump"})
        assert a["id"] == duplicate["id"]
        assert store.get(sid)["state"]["usage"]["tool_calls"] == 1
        with store.connection() as conn:
            conn.execute(
                "UPDATE research_session SET lease_until=now()-interval '1 second' WHERE id=%s", (sid,)
            )
        token2 = store.acquire(sid, "replacement")
        with pytest.raises(Conflict):
            store.complete(sid, token, a["id"], {}, {})
        assert store.get(sid)["state"]["usage"]["uncertain"] == 1
        store.release(sid, token2)
        token3 = store.acquire(sid, "another")
        assert store.get(sid)["state"]["status"] == "blocked"
        store.release(sid, token3)
        store.reconcile(sid, a["id"], "Read-only execution has no result; reservation retained")
        assert store.get(sid)["state"]["usage"]["uncertain"] == 0
        assert store.get(sid)["state"]["usage"]["tool_calls"] == 1

    def test_provider_uncertain_usage_is_never_blindly_retried(self, store, source):
        sid = store.create(Mission(objective="Bound calls", scope="controlled"))

        def uncertain(context):
            raise ProviderFailure("timeout", uncertain=True)

        row = Runner(store, source, ScriptedWorker(uncertain)).run(sid)
        assert row["state"]["outcome"] == "reconciliation_required"
        row = Runner(store, source, ScriptedWorker()).run(sid)
        assert row["state"]["usage"]["manager_calls"] == 1

    def test_source_failure_and_budget_stop_retain_partial_evidence(self, store, source):
        mission = Mission(objective="Bound reads", scope="controlled", limits=Limits(tool_calls=2))
        sid = store.create(mission)
        worker = ScriptedWorker(
            ("read_evidence", {"evidence_id": "trial"}),
            ("read_evidence", {"evidence_id": "missing"}),
            ("read_evidence", {"evidence_id": "limit"}),
        )
        row = Runner(store, source, worker).run(sid)
        assert row["state"]["outcome"] == "resource_limit"
        assert "trial" in row["state"]["evidence"]
        assert row["state"]["usage"]["tool_calls"] == 2
        assert (
            worker.contexts[-1]["observations"][-1]["result"]["meaning"]
            == "action failed; not negative evidence"
        )
        assert row["state"]["findings"] == []

    def test_invalid_citation_never_commits_findings(self, store, source):
        sid = store.create(
            Mission(objective="No invented citations", scope="controlled", limits=Limits(failures=1))
        )
        worker = ScriptedWorker(("finish", finding()))
        row = Runner(store, source, worker).run(sid)
        assert row["state"]["findings"] == []
        assert row["state"]["usage"]["failures"] == 1
        assert row["state"]["outcome"] == "resource_limit"

    def test_changed_source_refused_and_lease_released(self, store, source):
        sid = store.create(Mission(objective="Keep provenance", scope="controlled"))
        Runner(store, source, ScriptedWorker(("pause", {"reason": "Stopped"}))).run(sid)
        other = EvidenceSource(profile="different", bundle={"evidence": []})
        with pytest.raises(Conflict, match="source changed"):
            Runner(store, other, ScriptedWorker()).run(sid)
        assert store.get(sid)["lease_owner"] is None


def test_tool_timeout_is_bounded(source):
    import time

    source.execute = lambda *args: time.sleep(1)
    with pytest.raises(TimeoutError):
        bounded_read(source, "search", {"query": "pump"}, [], 0.01)


def test_worker_environment_has_no_paid_provider_selection(monkeypatch):
    from cosmai.research.workers import safe_environment

    monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture-value")
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-value")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    assert not set(safe_environment()) & {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CLAUDE_CODE_USE_BEDROCK"}


def test_discussion_cannot_invent_consent():
    from cosmai.research.state import initial_state

    state = initial_state(Mission(objective="No implicit consent", scope="controlled"))
    args = discussion()
    args["authorized_default"] = "Proceed without waiting"
    with pytest.raises(ValueError, match="implicit"):
        transition(state, "discuss", args, 0)


def test_sample_wrapper_uses_explicit_datasource(monkeypatch):
    from cosmai.research import tools

    calls = []

    def fake_sample(*args, **kwargs):
        calls.append(kwargs)
        raise RuntimeError("stop before source access")

    monkeypatch.setattr(tools, "sample_local", fake_sample)
    source = EvidenceSource(profile="isolated", database_url="postgresql://explicit-test")
    with pytest.raises(RuntimeError):
        source.execute("sample", {"per_cue": 1}, [])
    assert calls == [{"database_url": "postgresql://explicit-test"}]


def test_pending_discussion_cannot_be_removed_by_agenda_revision():
    from cosmai.research.state import ACTIONS, initial_state

    state = initial_state(Mission(objective="Respect participant boundary", scope="controlled"))
    transition(state, "assess", ACTIONS["assess"].model_validate(agenda()).model_dump(), 0)
    transition(state, "discuss", discussion(), 1)
    with pytest.raises(ValueError, match="still blocks"):
        transition(state, "assess", ACTIONS["assess"].model_validate(agenda()).model_dump(), 2)


@pytest.mark.postgres
def test_deadline_and_summary_survive_resume(store, source):
    sid = store.create(Mission(objective="Keep the envelope", scope="controlled"))
    Runner(store, source, ScriptedWorker(("assess", agenda()))).run(sid, steps=1)
    assert store.rebuild_summary(sid)["summary"] == "Two independent questions"
    with store.connection() as conn:
        conn.execute("UPDATE research_session SET deadline=now()-interval '1 second' WHERE id=%s", (sid,))
    row = Runner(store, source, ScriptedWorker()).run(sid)
    assert row["state"]["outcome"] == "resource_limit"
    assert row["state"]["usage"]["manager_calls"] == 1
    assert row["state"]["summary"] == "Two independent questions"


def test_manager_citations_are_not_treated_as_retrieved_evidence(source):
    records = [
        {
            "kind": "manager",
            "status": "done",
            "result": {"evidence": [{"evidence_id": "trial", "passage": "the pump delivered 0.2 g"}]},
        }
    ]
    result = source.execute("read_evidence", {"evidence_id": "limit"}, records)
    assert result["evidence"][0]["id"] == "limit"
