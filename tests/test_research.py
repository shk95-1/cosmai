"""Research execution boundaries and integrated recovery, using explicit isolated sources."""

from pathlib import Path
from typing import LiteralString, cast

import pytest
from psycopg.sql import SQL

from cosmai.research.runner import Runner, transition
from cosmai.research.state import Evidence, Limits, Mission, evidence_spans
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


def finding(source=None):
    return {
        "summary": "Pump dosing is promising for validation; no tolerance or demand conclusion.",
        "evidence": [
            {
                "evidence_id": "trial",
                "span_id": evidence_spans(source.evidence["trial"])[0]["id"] if source else "unknown",
            },
            {
                "evidence_id": "limit",
                "span_id": evidence_spans(source.evidence["limit"])[-1]["id"] if source else "unknown",
            },
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
        fresh = ScriptedWorker(("read_evidence", {"evidence_id": "limit"}), ("finish", finding(source)))
        row = Runner(Store(store.url), source, fresh).run(sid)
        assert row["state"]["status"] == "finished"
        assert fresh.contexts[0]["state"]["evidence"]["trial"]["passage"]
        assert fresh.contexts[1]["observations"][-1]["result"]["evidence_refs"][0]["evidence_id"] == "limit"
        assert len(row["state"]["findings"]) == 1
        store.feedback(sid, "Prioritize transit reliability instead of dosing accuracy")
        row = store.get(sid)
        assert row["state"]["goal_revision"] == 0
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
        Runner(
            store,
            source,
            ScriptedWorker(
                lambda c: interpreted(
                    c,
                    "goal_change",
                    affected_tasks=["choice"],
                    affected_discussions=["strategy"],
                    preferences=["Reliability"],
                )
            ),
        ).run(sid, steps=1)
        store.feedback(sid, "Choose speed", "strategy")
        state = store.get(sid)["state"]
        assert state["goal_revision"] == 1
        assert not state["feedback"][-1]["applied"]
        assert state["tasks"]["independent"]["status"] == "ready"

    def test_feedback_during_manager_rejects_old_decision(self, store, source):
        sid = store.create(Mission(objective="Test revision", scope="controlled"))

        def interrupted(context):
            store.feedback(sid, "Prefer reliability")
            return proposal(context, "pause", {"reason": "Old plan must not apply"})

        worker = ScriptedWorker(interrupted, interpreted, ("pause", {"reason": "New goal considered"}))
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
            ("read_evidence", {"evidence_id": "trial"}),
            interpreted,
            ("pause", {"reason": "Reconsidered old observation"}),
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
        worker = ScriptedWorker(("finish", finding(source)))
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


@pytest.mark.postgres
def test_feedback_between_context_read_and_reservation_rebuilds_context(store, source):
    sid = store.create(Mission(objective="Rebuild stale context", scope="controlled"))
    schemas = source.schemas
    pending = [True]

    def racing_schemas():
        if pending:
            pending.pop()
            store.feedback(sid, "A priority change during context construction")
        return schemas()

    source.schemas = racing_schemas
    worker = ScriptedWorker(interpreted, ("pause", {"reason": "Current priority considered"}))
    row = Runner(store, source, worker).run(sid)
    assert worker.contexts[0]["goal_revision"] == 0
    assert worker.contexts[0]["state"]["feedback"][-1]["status"] == "pending"
    assert row["state"]["usage"]["manager_calls"] == 2
    assert row["state"]["usage"]["failures"] == 0


def test_discussion_blocks_transitively_dependent_agenda():
    from cosmai.research.state import ACTIONS, initial_state

    state = initial_state(Mission(objective="Respect dependency closure", scope="controlled"))
    tasks = agenda()
    tasks["tasks"][1]["dependencies"] = ["choice"]
    transition(state, "assess", ACTIONS["assess"].model_validate(tasks).model_dump(), 0)
    transition(state, "discuss", discussion(), 1)
    assert state["status"] == "blocked"
    assert state["outcome"] == "participant_input_required"


def test_span_selection_preserves_unicode_and_rejects_changed_source():
    from cosmai.research.state import evidence_spans, resolve_citations

    raw = Evidence(
        id="unicode",
        source="synthetic",
        locator="fixture",
        captured_at="now",
        passage="Original \u110f\u110f\u110f is not \u314b\u314b\u314b.",
    ).frozen()
    ref = {"evidence_id": "unicode", "span_id": evidence_spans(raw)[0]["id"]}
    resolved = resolve_citations([ref], {"unicode": raw})[0]
    assert resolved["passage"] == raw["passage"]
    changed = {**raw, "passage": "Changed text"}
    with pytest.raises(ValueError):
        resolve_citations([ref], {"unicode": changed})
    with pytest.raises(ValueError):
        resolve_citations([{**ref, "span_id": "fabricated"}], {"unicode": raw})


def test_frozen_capabilities_have_executable_analysis_path():
    from analysis.discovery.core import freeze

    snapshot = freeze(
        {"finished_at": "2026-11-02"},
        [{"doc_id": "d1", "source": "fixture", "text": "No personal usage observed.", "published_at": None}],
    )
    source = EvidenceSource(profile="snapshot", bundle=snapshot)
    assert "sample" in source.schemas() and "analyze" in source.schemas()
    sampled = source.execute("sample", {}, [])
    assert sampled["snapshot"] == snapshot
    plain = EvidenceSource(profile="plain", bundle={"evidence": []})
    assert "analyze" not in plain.schemas() and "corroborate" not in plain.schemas()


def test_discovery_schemas_describe_nested_required_fields():
    from cosmai.research.state import ACTIONS

    schema = ACTIONS["corroborate"].model_json_schema()
    assert "observations" in str(schema) and "all_of" in str(schema)
    with pytest.raises(ValueError):
        ACTIONS["analyze"].model_validate({"sample_action": "x", "review": {}})


def interpreted(context, kind="information", **kwargs):
    item = next(f for f in context["state"]["feedback"] if f.get("status") == "pending")
    return proposal(
        context,
        "integrate_feedback",
        {
            "feedback_id": item["id"],
            "kind": kind,
            "interpretation": "Controlled interpretation",
            "response": "Input considered",
            **kwargs,
        },
    )


@pytest.mark.postgres
def test_answer_a_preserves_b_and_source_question_preserves_agenda(store, source):
    sid = store.create(Mission(objective="Selective participation", scope="controlled"))
    tasks = agenda()
    a = discussion()
    b = {**discussion(), "id": "audience", "dependent_tasks": ["independent"]}
    Runner(store, source, ScriptedWorker(("assess", tasks), ("discuss", a), ("discuss", b))).run(sid)
    before = store.get(sid)
    store.feedback(sid, "Favor differentiation")  # no technical event label or discussion ID
    worker = ScriptedWorker(lambda c: interpreted(c, "decision", discussion_id="strategy"))
    row = Runner(store, source, worker).run(sid, steps=1)
    assert row["state"]["discussions"]["strategy"]["status"] == "answered"
    assert row["state"]["discussions"]["audience"]["status"] == "pending"
    assert row["state"]["tasks"]["choice"]["status"] == "ready"
    assert row["state"]["tasks"]["independent"]["status"] == "blocked"
    assert row["state"]["goal_revision"] == 0
    store.feedback(sid, "Where is that source?")
    after = Runner(store, source, ScriptedWorker(interpreted)).run(sid, steps=1)
    assert after["state"]["tasks"] == row["state"]["tasks"]
    assert after["state"]["discussions"] == row["state"]["discussions"]
    assert after["deadline"] == before["deadline"]
    assert after["state"]["mission"]["limits"] == before["state"]["mission"]["limits"]


@pytest.mark.postgres
def test_optional_discussion_allows_unassigned_read_and_finish(store, source):
    sid = store.create(Mission(objective="Optional ideas", scope="controlled"))
    optional = {**discussion(), "dependent_tasks": []}
    row = Runner(
        store,
        source,
        ScriptedWorker(
            ("discuss", optional),
            ("read_evidence", {"evidence_id": "trial"}),
            ("read_evidence", {"evidence_id": "limit"}),
            ("finish", finding(source)),
        ),
    ).run(sid)
    assert row["state"]["status"] == "finished"
    assert row["state"]["discussions"]["strategy"]["status"] == "pending"
    assert row["state"]["usage"]["failures"] == 0


@pytest.mark.postgres
def test_validation_feedback_names_missing_field_and_allows_bounded_correction(store, source):
    sid = store.create(Mission(objective="Repair malformed action", scope="controlled"))
    worker = ScriptedWorker(("read_evidence", {}), ("read_evidence", {"evidence_id": "trial"}))
    row = Runner(store, source, worker).run(sid, steps=1)
    error = worker.contexts[-1]["recent_provider_errors"][-1]
    assert error["details"][0]["field"] == "evidence_id"
    assert "input" not in error["details"][0]
    assert "trial" in row["state"]["evidence"]
    assert row["state"]["usage"]["manager_calls"] == 2
    assert row["state"]["usage"]["failures"] == 1


def test_frozen_snapshot_analyze_extracts_review_spans():
    from analysis.discovery.core import freeze
    from cosmai.research.tools import snapshot_evidence

    snapshot = freeze(
        {"finished_at": "2026-11-02"},
        [{"doc_id": "d1", "source": "fixture", "text": "No personal usage observed.", "published_at": None}],
    )
    source = EvidenceSource(profile="snapshot", bundle=snapshot)
    sampled = source.execute("sample", {}, [])
    ref = evidence_spans(snapshot_evidence(snapshot)["d1"])[0]["id"]
    args = {
        "sample_action": "sample1",
        "review": {
            "snapshot_sha256": snapshot["snapshot_sha256"],
            "reviewer": "fixture",
            "interventions": [],
            "candidates": {},
            "observations": [
                {"doc_id": "d1", "disposition": "off_topic", "reason": "No usage", "span_id": ref}
            ],
        },
    }
    records = [{"id": "sample1", "kind": "sample", "status": "done", "result": sampled}]
    result = source.execute("analyze", args, records)
    assert result["selection"]["status"] == "no_qualified_candidate"
    args["review"]["observations"][0]["span_id"] = "fabricated"
    with pytest.raises(ValueError, match="span_id"):
        source.execute("analyze", args, records)


def test_literature_access_is_scoped_counted_and_requires_discovered_ids(monkeypatch):
    from cosmai.research import literature

    calls = []

    def fake(params):
        calls.append(params)
        return {
            "response_sha256": "response",
            "resultList": {
                "result": [
                    {"source": "MED", "id": "123", "title": "Trial", "abstractText": "Original result."}
                ]
            },
        }

    monkeypatch.setattr(literature, "fetch", fake)
    src = EvidenceSource(profile="public", bundle={"literature": {"max_requests": 2}})
    records = [{"id": "s", "kind": "literature_search", "status": "reserved", "result": None}]
    result = src.execute("literature_search", {"query": "moisturizer"}, records)
    records[0].update(status="done", result=result)
    args = {"source": "MED", "article_id": "123"}
    records.append({"id": "r", "kind": "literature_read", "status": "reserved", "result": None})
    read = src.execute("literature_read", args, records)
    assert read["evidence"][0]["passage"] == "Original result."
    with pytest.raises(ValueError, match="discovered"):
        src.execute("literature_read", {**args, "article_id": "456"}, records)
    records.append({"kind": "literature_read", "status": "reserved"})
    with pytest.raises(ValueError, match="allowance"):
        src.execute("literature_read", args, records)
    assert len(calls) == 2
    assert "literature_search" not in EvidenceSource(profile="offline", bundle={"evidence": []}).schemas()


def test_information_cannot_invalidate_work_and_stale_decision_cannot_apply():
    from cosmai.research.participation import integrate
    from cosmai.research.state import ACTIONS, initial_state

    state = initial_state(Mission(objective="Keep local effects", scope="controlled"))
    transition(state, "assess", ACTIONS["assess"].model_validate(agenda()).model_dump(), 0)
    transition(state, "discuss", discussion(), 1)
    state["feedback"] = [
        {
            "id": "f",
            "status": "pending",
            "discussion": "strategy",
            "text": "Answer",
            "observed_revision": 2,
            "discussion_revision": 1,
        }
    ]
    args = (
        ACTIONS["integrate_feedback"]
        .model_validate(
            {
                "feedback_id": "f",
                "kind": "information",
                "interpretation": "Source question",
                "response": "A source",
                "affected_tasks": ["choice"],
            }
        )
        .model_dump()
    )
    with pytest.raises(ValueError, match="genuine"):
        integrate(state, args)
    args.update(kind="decision", affected_tasks=[])
    state["discussions"]["strategy"]["status"] = "superseded"
    with pytest.raises(ValueError, match="pending"):
        integrate(state, args)


@pytest.mark.postgres
def test_scoped_goal_change_keeps_independent_old_goal_discussion_answerable(store, source):
    sid = store.create(Mission(objective="Scoped change", scope="controlled"))
    Runner(
        store,
        source,
        ScriptedWorker(
            ("assess", agenda()),
            ("discuss", discussion()),
            ("discuss", {**discussion(), "id": "other", "dependent_tasks": ["independent"]}),
        ),
    ).run(sid)
    store.feedback(sid, "Change the strategy question, keep the other question")
    Runner(
        store,
        source,
        ScriptedWorker(
            lambda c: interpreted(
                c,
                "goal_change",
                affected_tasks=["choice"],
                affected_discussions=["strategy"],
                preferences=["Revisit strategy"],
            )
        ),
    ).run(sid, steps=1)
    store.feedback(sid, "Here is my independent answer", "other")
    row = Runner(
        store, source, ScriptedWorker(lambda c: interpreted(c, "decision", discussion_id="other"))
    ).run(sid, steps=1)
    assert row["state"]["goal_revision"] == 1
    assert row["state"]["discussions"]["other"]["goal_revision"] == 0
    assert row["state"]["discussions"]["other"]["status"] == "answered"
    assert row["state"]["discussions"]["strategy"]["status"] == "superseded"


def test_repeated_literature_read_preserves_original_capture(monkeypatch):
    from cosmai.research import literature

    calls = []

    def fake(params):
        calls.append(params)
        return {
            "response_sha256": "hash",
            "resultList": {
                "result": [{"id": "123", "source": "MED", "title": "Trial", "abstractText": "Original text."}]
            },
        }

    monkeypatch.setattr(literature, "fetch", fake)
    src = EvidenceSource(profile="public", bundle={"literature": {"max_requests": 4}})
    records = [
        {
            "kind": "literature_search",
            "status": "done",
            "result": {"papers": [{"id": "123", "source": "MED"}]},
        }
    ]
    args = {"source": "MED", "article_id": "123"}
    first = src.execute("literature_read", args, records)
    records.append({"kind": "literature_read", "status": "done", "result": first})
    second = src.execute("literature_read", args, records)
    assert second["evidence"] == first["evidence"]
    assert len(calls) == 1


@pytest.mark.postgres
class TestTerminalParticipation:
    def test_input_automatically_continues_and_preserves_other_question(self, store, source):
        from cosmai.research.cli import participate

        sid = store.create(Mission(objective="Selective terminal participation", scope="controlled"))
        Runner(
            store,
            source,
            ScriptedWorker(
                ("assess", agenda()),
                ("discuss", discussion()),
                ("discuss", {**discussion(), "id": "other", "dependent_tasks": ["independent"]}),
            ),
        ).run(sid)
        before = store.get(sid)
        worker = ScriptedWorker(
            lambda c: interpreted(c, "decision", discussion_id="strategy"),
            ("finish", {**finding(), "evidence": [], "outcome": "participant_input_required"}),
        )
        answers = iter(["Favor differentiation", ""])
        output = []
        row = participate(
            Runner(store, source, worker), sid, read=lambda _: next(answers), write=output.append
        )
        assert row["state"]["feedback"][0]["text"] == "Favor differentiation"
        assert row["state"]["feedback"][0]["observed_revision"] == before["revision"]
        assert row["state"]["discussions"]["strategy"]["status"] == "answered"
        assert row["state"]["discussions"]["other"] == before["state"]["discussions"]["other"]
        assert row["deadline"] == before["deadline"]
        assert row["state"]["mission"]["limits"] == before["state"]["mission"]["limits"]
        assert row["state"]["goal_revision"] == 0
        assert "Recommendation:" in "\n".join(output)
        assert "Input considered" in "\n".join(output)

    @pytest.mark.parametrize("answer", ["", "   ", None])
    def test_exit_does_not_supply_consent_or_start_a_finished_session(self, store, source, answer):
        from cosmai.research.cli import participate

        sid = store.create(Mission(objective="No implicit consent", scope="controlled"))
        Runner(
            store,
            source,
            ScriptedWorker(("finish", {**finding(), "evidence": [], "outcome": "evidence_limit"})),
        ).run(sid)
        before = store.get(sid)

        def read(_):
            if answer is None:
                raise EOFError
            return answer

        row = participate(Runner(store, source, ScriptedWorker()), sid, read=read, write=lambda _: None)
        assert row["revision"] == before["revision"]
        assert row["state"] == before["state"]

    def test_changed_display_requires_review_before_resubmission(self, store, source):
        from cosmai.research.cli import participate

        sid = store.create(Mission(objective="Protect displayed state", scope="controlled"))
        Runner(
            store,
            source,
            ScriptedWorker(("finish", {**finding(), "evidence": [], "outcome": "evidence_limit"})),
        ).run(sid)
        answers = iter(["/sources", "The old answer", ""])

        def read(_):
            answer = next(answers)
            if answer == "The old answer":
                store.feedback(sid, "An independent contribution arrived")
            return answer

        output = []
        row = participate(Runner(store, source, ScriptedWorker()), sid, read=read, write=output.append)
        assert [f["text"] for f in row["state"]["feedback"]] == ["An independent contribution arrived"]
        assert "not submitted" in "\n".join(output)

    def test_delayed_input_is_saved_without_renewing_expired_deadline(self, store, source):
        from cosmai.research.cli import participate

        sid = store.create(Mission(objective="Delayed participant", scope="controlled"))
        Runner(
            store,
            source,
            ScriptedWorker(("finish", {**finding(), "evidence": [], "outcome": "evidence_limit"})),
        ).run(sid)
        before = store.get(sid)

        def read(_):
            with store.connection() as conn:
                conn.execute(
                    "UPDATE research_session SET deadline=clock_timestamp()-interval '1 second' WHERE id=%s",
                    (sid,),
                )
            return "Show me the source"

        output = []
        row = participate(Runner(store, source, ScriptedWorker()), sid, read=read, write=output.append)
        assert row["state"]["feedback"][0]["text"] == "Show me the source"
        assert row["state"]["feedback"][0]["status"] == "pending"
        assert row["state"]["usage"] == before["state"]["usage"]
        assert row["deadline"] < before["deadline"]
        assert "deadline" in "\n".join(output)

    @pytest.mark.parametrize("blocker", ["lease", "uncertain", "budget"])
    def test_unavailable_execution_does_not_prompt_or_call_worker(self, store, source, blocker):
        from psycopg.types.json import Jsonb

        from cosmai.research.cli import participate

        sid = store.create(Mission(objective="No hidden allowance", scope="controlled"))
        token = store.acquire(sid, "other-runner")
        if blocker != "lease":
            store.release(sid, token)
            row = store.get(sid)
            usage = row["state"]["usage"]
            if blocker == "uncertain":
                usage["uncertain"] = 1
            else:
                usage["manager_calls"] = row["state"]["mission"]["limits"]["manager_calls"]
            with store.connection() as conn:
                conn.execute("UPDATE research_session SET state=%s WHERE id=%s", (Jsonb(row["state"]), sid))
        before = store.get(sid)

        def read(_):
            pytest.fail("unavailable execution should not solicit input")

        row = participate(Runner(store, source, ScriptedWorker()), sid, read=read, write=lambda _: None)
        assert row["revision"] == before["revision"]
        assert row["state"] == before["state"]

    def test_input_survives_continuation_failure(self, store, source):
        from cosmai.research.cli import participate

        sid = store.create(Mission(objective="Durable input", scope="controlled"))
        Runner(
            store,
            source,
            ScriptedWorker(("finish", {**finding(), "evidence": [], "outcome": "evidence_limit"})),
        ).run(sid)

        def failure(c):
            raise RuntimeError("simulated interruption")

        with pytest.raises(RuntimeError, match="simulated"):
            participate(
                Runner(store, source, ScriptedWorker(failure)),
                sid,
                read=lambda _: "A voluntary idea",
                write=lambda _: None,
            )
        row = store.get(sid)
        assert row["state"]["feedback"][0]["text"] == "A voluntary idea"
        assert row["state"]["feedback"][0]["status"] == "pending"
        assert row["lease_until"] is None

        def no_prompt(_):
            pytest.fail("interrupted execution must reconcile before accepting more input")

        recovered = participate(
            Runner(Store(store.url), source, ScriptedWorker()), sid, read=no_prompt, write=lambda _: None
        )
        assert recovered["state"]["outcome"] == "reconciliation_required"
        assert recovered["state"]["feedback"] == row["state"]["feedback"]
        assert recovered["state"]["usage"]["manager_calls"] == row["state"]["usage"]["manager_calls"]
        assert recovered["deadline"] == row["deadline"]

    def test_source_question_gets_exact_citation_in_terminal(self, store, source):
        from cosmai.research.cli import participate

        sid = store.create(Mission(objective="Source visibility", scope="controlled"))
        Runner(
            store,
            source,
            ScriptedWorker(
                ("read_evidence", {"evidence_id": "trial"}),
                ("read_evidence", {"evidence_id": "limit"}),
                ("finish", finding(source)),
            ),
        ).run(sid)
        worker = ScriptedWorker(lambda c: interpreted(c), ("finish", finding(source)))
        answers = iter(["Show the source", "/sources", ""])
        output = []
        row = participate(
            Runner(store, source, worker), sid, read=lambda _: next(answers), write=output.append
        )
        assert row["state"]["feedback"][0]["interpretation"]["kind"] == "information"
        assert "Source fixture:trial:" in "\n".join(output)
        assert source.evidence["trial"]["passage"] in "\n".join(output)


@pytest.mark.postgres
def test_participate_cli_runs_then_accepts_stdin_and_resumes(store, source, monkeypatch, tmp_path, capsys):
    import io
    import json

    from cosmai.cli import main
    from cosmai.research import workers

    sid = store.create(Mission(objective="Terminal entry point", scope="controlled"))
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(source.bundle))
    worker = ScriptedWorker(
        ("read_evidence", {"evidence_id": "trial"}),
        ("read_evidence", {"evidence_id": "limit"}),
        ("finish", finding(source)),
        lambda c: interpreted(c),
        ("finish", finding(source)),
    )
    monkeypatch.setattr(workers, "CodexWorker", lambda: worker)
    monkeypatch.setenv("COSMAI_RESEARCH_SESSION_URL", store.url)
    monkeypatch.setattr("sys.stdin", io.StringIO("Show the source\n/sources\n\n"))
    assert (
        main(
            ["research", "participate", sid, "--evidence-file", str(path), "--source-profile", source.profile]
        )
        == 0
    )
    row = store.get(sid)
    assert row["state"]["feedback"][0]["interpretation"]["kind"] == "information"
    assert row["state"]["usage"]["manager_calls"] == 5
    assert "Source fixture:trial:" in capsys.readouterr().out


def fulltext_unicode():
    return Path("tests/fixtures/research_fulltext_unicode.txt").read_text(encoding="utf-8")


def fulltext_xml(text=None):
    if text is None:
        text = f"Original method: {fulltext_unicode()} with glycerol."
    return (
        '<article><front><article-meta><article-id pub-id-type="pmcid">PMC123</article-id>'
        '</article-meta></front><body><sec id="methods"><title>Methods</title><p>'
        + text
        + "</p></sec><sec><title>Results</title><p>No superiority established.</p>"
        "</sec></body><back><sec><title>Conflicts</title><p>Industry funding.</p></sec></back></article>"
    ).encode()


def fulltext_records():
    return [
        {
            "id": "search",
            "kind": "literature_search",
            "status": "done",
            "result": {
                "papers": [
                    {"id": "456", "source": "MED", "pmcid": "PMC123", "isOpenAccess": "Y", "title": "Trial"}
                ]
            },
        }
    ]


def test_fulltext_requires_discovery_and_reuses_immutable_capture(monkeypatch):
    from cosmai.research import literature

    calls = []

    def fetch(pmcid):
        calls.append(pmcid)
        return fulltext_xml()

    monkeypatch.setattr(literature, "fetch_xml", fetch, raising=False)
    src = EvidenceSource(profile="public", bundle={"literature": {"max_requests": 12, "fulltext": True}})
    with pytest.raises(ValueError, match="discovered"):
        src.execute("literature_fulltext", {"pmcid": "PMC123"}, [])
    records = fulltext_records()
    first = src.execute("literature_fulltext", {"pmcid": "PMC123"}, records)
    records.append({"id": "document", "kind": "literature_fulltext", "status": "done", "result": first})
    again = src.execute("literature_fulltext", {"pmcid": "PMC123"}, records)
    assert again["document"] == first["document"]
    assert first["network_requests"] == 1 and again["network_requests"] == 0
    assert calls == ["PMC123"]
    part = src.execute(
        "literature_section",
        {"fulltext_action": "document", "section_id": first["sections"][0]["id"]},
        records,
    )
    assert part["evidence"][0]["passage"] == f"MethodsOriginal method: {fulltext_unicode()} with glycerol."
    assert part["document_sha256"] == first["document_sha256"]
    assert part["network_requests"] == 0
    assert first["sections"][-1]["title"] == "Conflicts"
    assert "literature_fulltext" not in EvidenceSource(profile="offline", bundle={"evidence": []}).schemas()
    assert (
        "literature_fulltext"
        not in EvidenceSource(profile="abstracts", bundle={"literature": {"max_requests": 12}}).schemas()
    )


def test_fulltext_rejects_fabricated_sections_and_changed_capture(monkeypatch):
    from cosmai.research import literature

    monkeypatch.setattr(literature, "fetch_xml", lambda _: fulltext_xml(), raising=False)
    src = EvidenceSource(profile="public", bundle={"literature": {"max_requests": 12, "fulltext": True}})
    records = fulltext_records()
    doc = src.execute("literature_fulltext", {"pmcid": "PMC123"}, records)
    records.append({"id": "document", "kind": "literature_fulltext", "status": "done", "result": doc})
    with pytest.raises(ValueError, match="section"):
        src.execute("literature_section", {"fulltext_action": "document", "section_id": "invented"}, records)
    with pytest.raises(ValueError, match="capture"):
        src.execute(
            "literature_section",
            {"fulltext_action": "unknown", "section_id": doc["sections"][0]["id"]},
            records,
        )
    doc["document"]["xml"] = doc["document"]["xml"].replace("glycerol", "ceramide")
    with pytest.raises(ValueError, match="changed"):
        src.execute(
            "literature_section",
            {"fulltext_action": "document", "section_id": doc["sections"][0]["id"]},
            records,
        )


@pytest.mark.parametrize(
    "xml",
    [
        fulltext_xml().replace(b"PMC123", b"PMC999"),
        b'<!DOCTYPE article [<!ENTITY x "boom">]><article>&x;</article>',
        b"<article>",
    ],
)
def test_fulltext_rejects_article_mismatch_entities_and_malformed_xml(monkeypatch, xml):
    from cosmai.research import literature

    monkeypatch.setattr(literature, "fetch_xml", lambda _: xml, raising=False)
    src = EvidenceSource(profile="public", bundle={"literature": {"max_requests": 12, "fulltext": True}})
    with pytest.raises(ValueError):
        src.execute("literature_fulltext", {"pmcid": "PMC123"}, fulltext_records())


def test_fulltext_long_sections_are_all_addressable_without_normalization(monkeypatch):
    from cosmai.research import literature

    text = f"A{fulltext_unicode()}<italic>B</italic>" * 2000
    monkeypatch.setattr(literature, "fetch_xml", lambda _: fulltext_xml(text), raising=False)
    src = EvidenceSource(profile="public", bundle={"literature": {"max_requests": 12, "fulltext": True}})
    records = fulltext_records()
    doc = src.execute("literature_fulltext", {"pmcid": "PMC123"}, records)
    records.append({"id": "document", "kind": "literature_fulltext", "status": "done", "result": doc})
    methods = [p for p in doc["sections"] if p["title"] == "Methods"]
    passages = [
        src.execute("literature_section", {"fulltext_action": "document", "section_id": p["id"]}, records)[
            "evidence"
        ][0]["passage"]
        for p in methods
    ]
    assert len(methods) > 1
    assert all(len(p) <= 8000 for p in passages)
    assert "".join(passages) == "Methods" + f"A{fulltext_unicode()}B" * 2000


def test_fulltext_discovery_eligibility_and_conservative_quota_precede_http(monkeypatch):
    from cosmai.research import literature

    def no_http(_):
        pytest.fail("request must not be sent")

    monkeypatch.setattr(literature, "fetch_xml", no_http, raising=False)
    src = EvidenceSource(profile="public", bundle={"literature": {"max_requests": 1, "fulltext": True}})
    records = fulltext_records()
    records[0]["result"]["papers"][0]["isOpenAccess"] = "N"
    with pytest.raises(ValueError, match="eligible"):
        src.execute("literature_fulltext", {"pmcid": "PMC123"}, records)
    records.append({"kind": "literature_fulltext", "status": "uncertain"})
    with pytest.raises(ValueError, match="allowance"):
        src.execute("literature_fulltext", {"pmcid": "PMC123"}, records)


def test_literature_transport_fulltext_fixed_host_no_redirect_and_size_limit(monkeypatch):
    import httpx

    from cosmai.research import literature

    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://other.invalid/"})

    client = httpx.Client
    monkeypatch.setattr(
        literature.httpx, "Client", lambda **kw: client(transport=httpx.MockTransport(handle), **kw)
    )
    with pytest.raises(httpx.HTTPStatusError):
        literature.fetch_xml("PMC123")
    assert len(requests) == 1
    assert requests[0].url.host == "www.ebi.ac.uk"
    assert requests[0].url.path.endswith("/PMC123/fullTextXML")
    with pytest.raises(ValueError):
        literature.fetch_xml("../other")
    assert len(requests) == 1
    monkeypatch.setattr(
        literature.httpx,
        "Client",
        lambda **kw: client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 1_000_001)), **kw
        ),
    )
    with pytest.raises(ValueError, match="1 MB"):
        literature.fetch_xml("PMC123")


@pytest.mark.postgres
def test_worker_indexes_fulltext_then_reads_selected_section_with_bounded_context(store, monkeypatch):
    import json

    from cosmai.research import literature
    from cosmai.research.state import evidence_spans

    monkeypatch.setattr(
        literature,
        "fetch",
        lambda _: {
            "response_sha256": "search-hash",
            "resultList": {"result": fulltext_records()[0]["result"]["papers"]},
        },
    )
    monkeypatch.setattr(
        literature, "fetch_xml", lambda _: fulltext_xml("Evidence text. " * 15000), raising=False
    )
    src = EvidenceSource(profile="public", bundle={"literature": {"max_requests": 12, "fulltext": True}})
    sid = store.create(Mission(objective="Inspect a useful full method", scope="controlled XML"))

    def choose(context):
        observation = context["observations"][-1]
        assert context["remaining_resources"]["literature_actions"] == 10
        assert "document" not in observation["result"]
        assert "document_access" in observation["result"]
        assert "Evidence text." not in json.dumps(context)
        return proposal(
            context,
            "literature_section",
            {
                "fulltext_action": observation["execution_id"],
                "section_id": observation["result"]["sections"][0]["id"],
            },
        )

    def finish(context):
        e = next(iter(context["state"]["evidence"].values()))
        return proposal(
            context,
            "finish",
            {**finding(), "evidence": [{"evidence_id": e["id"], "span_id": evidence_spans(e)[0]["id"]}]},
        )

    worker = ScriptedWorker(
        ("literature_search", {"query": "method"}),
        ("literature_fulltext", {"pmcid": "PMC123"}),
        choose,
        finish,
    )
    row = Runner(store, src, worker).run(sid)
    assert row["state"]["outcome"] == "findings"
    assert row["state"]["usage"]["manager_calls"] == 4
    capture = next(a for a in Store(store.url).actions(sid) if a["kind"] == "literature_fulltext")
    assert len(capture["result"]["document"]["xml"]) > 150000
    assert len(json.dumps(worker.contexts[-1])) < 150000


def test_fulltext_tables_remain_inspectable_with_cell_structure(monkeypatch):
    from cosmai.research import literature

    table = (
        "<table-wrap><label>Table 1</label><caption><p>Composition</p></caption>"
        "<table><tr><th>Ingredient</th><th>Percent</th></tr>"
        "<tr><td>Glycerol</td><td>3.0</td></tr></table></table-wrap>"
    )
    xml = fulltext_xml().replace(b"</body>", table.encode() + b"</body>")
    monkeypatch.setattr(literature, "fetch_xml", lambda _: xml)
    src = EvidenceSource(profile="public", bundle={"literature": {"max_requests": 12, "fulltext": True}})
    records = fulltext_records()
    doc = src.execute("literature_fulltext", {"pmcid": "PMC123"}, records)
    records.append({"id": "document", "kind": "literature_fulltext", "status": "done", "result": doc})
    part = next(p for p in doc["sections"] if p["representation"] == "table_xml")
    read = src.execute(
        "literature_section", {"fulltext_action": "document", "section_id": part["id"]}, records
    )
    assert "<tr><td>Glycerol</td><td>3.0</td></tr>" in read["evidence"][0]["passage"]
    assert "<th>Percent</th>" in read["evidence"][0]["passage"]


def test_observation_evidence_projection_is_lossless_and_keeps_unavailable_content(source):
    from copy import deepcopy

    from cosmai.research.fulltext import context_result

    evidence = source.evidence["trial"]
    result = {"evidence": [evidence], "limitations": ["Original limitation"]}
    original = deepcopy(result)
    projected = context_result("read_evidence", result, source.evidence)
    assert "evidence" not in projected
    assert projected["evidence_refs"] == [{"evidence_id": evidence["id"], "sha256": evidence["sha256"]}]
    assert "state.evidence" in projected["evidence_access"]
    assert projected["limitations"] == result["limitations"]
    assert result == original
    assert context_result("read_evidence", result, {}) == result
    changed = deepcopy(evidence)
    changed["passage"] = "Different evidence must not silently replace the observed passage."
    assert context_result("read_evidence", result, {evidence["id"]: changed}) == result


@pytest.mark.postgres
def test_full_passages_remain_available_when_duplicate_observations_would_overflow(store):
    import json

    items = [
        Evidence(
            id=f"large-{i}",
            source="Controlled",
            locator=f"fixture:{i}",
            captured_at="2026-11-02",
            passage=("Controlled evidence " * 500)[:8000],
        ).frozen()
        for i in range(6)
    ]
    source = EvidenceSource(profile="context-regression", bundle={"evidence": items})
    sid = store.create(Mission(objective="Preserve all inspected evidence", scope="controlled regression"))

    def finish(context):
        assert len(json.dumps(context)) < 150_000
        for item in items:
            assert context["state"]["evidence"][item["id"]] == item
            assert context["evidence_spans"][item["id"]]
        for observation in context["observations"]:
            assert "evidence" not in observation["result"]
            assert observation["result"]["evidence_refs"]
        citation = context["evidence_spans"][items[0]["id"]][0]
        return proposal(
            context,
            "finish",
            {**finding(), "evidence": [{"evidence_id": items[0]["id"], "span_id": citation["id"]}]},
        )

    worker = ScriptedWorker(*[("read_evidence", {"evidence_id": item["id"]}) for item in items], finish)
    row = Runner(store, source, worker).run(sid)
    assert row["state"]["outcome"] == "findings"
    raw = [a for a in Store(store.url).actions(sid) if a["kind"] == "read_evidence"]
    assert [a["result"]["evidence"][0] for a in raw] == items
    restored = Runner(Store(store.url), source, worker).context(sid, "offline-restart")
    assert restored["state"]["evidence"] == row["state"]["evidence"]


def test_participant_brief_preserves_findings_questions_and_source_access(source):
    from cosmai.research.cli import participation_sources, participation_text
    from cosmai.research.state import initial_state

    state = initial_state(Mission(objective="A useful research question", scope="controlled"))
    citation = {"evidence_id": "trial", "span_id": "original", "passage": source.evidence["trial"]["passage"]}
    state.update(
        evidence=source.evidence,
        summary="Qualified finding with a counterexample.",
        summary_goal_revision=0,
        findings=[{**finding(), "evidence": [citation, citation], "goal_revision": 0}],
        discussions={
            d["id"]: {**d, "status": "pending"}
            for d in [
                discussion(),
                {**discussion(), "id": "independent", "question": "Independent question B?"},
            ]
        },
    )
    row = {"state": state, "revision": 42, "id": "internal-session-id", "deadline": None}
    brief = participation_text(row)
    assert state["summary"] in brief
    assert all(u in brief for u in state["findings"][-1]["uncertainties"])
    assert state["findings"][-1]["next_validation"] in brief
    for packet in state["discussions"].values():
        for key in (
            "question",
            "why_now",
            "finding",
            "uncertainty",
            "recommendation",
            "strongest_objection",
            "contribution",
            "independent_work",
        ):
            assert packet[key] in brief
        assert all(option in brief for option in packet["options"])
    assert "fixture:trial" in brief and "/sources" in brief
    assert source.evidence["trial"]["passage"] not in brief
    assert "internal-session-id" not in brief and source.evidence["trial"]["sha256"] not in brief
    assert "12" in brief and "Deadline" in brief
    passages = participation_sources(row)
    assert passages.count(source.evidence["trial"]["passage"]) == 1


def test_participant_brief_marks_stale_findings_and_honest_stop(source):
    from cosmai.research.cli import participation_text
    from cosmai.research.state import initial_state

    state = initial_state(Mission(objective="New question", scope="controlled"))
    state.update(
        summary="Old finding",
        summary_goal_revision=0,
        goal_revision=1,
        findings=[{**finding(), "evidence": [], "goal_revision": 0}],
        status="paused",
        outcome="resource_limit",
        reason="Context limit reached before synthesis",
    )
    text = participation_text({"state": state, "deadline": None})
    assert "earlier objective" in text.lower()
    assert "Context limit reached before synthesis" in text
    assert "Old finding" in text
    state.update(outcome="evidence_limit", reason=state["findings"][-1]["next_validation"])
    text = participation_text({"state": state, "deadline": None})
    assert "Evidence limit reached" in text
    assert text.count(state["reason"]) == 1


@pytest.mark.postgres
def test_local_source_inspection_preserves_state_limits_and_consumes_no_calls(store, source):
    from cosmai.research.cli import participate

    sid = store.create(Mission(objective="Inspect citations without another worker", scope="controlled"))
    Runner(
        store,
        source,
        ScriptedWorker(
            ("read_evidence", {"evidence_id": "trial"}),
            ("read_evidence", {"evidence_id": "limit"}),
            ("finish", finding(source)),
        ),
    ).run(sid)
    before = store.get(sid)
    actions = store.actions(sid)
    output = []
    answers = iter(["/sources", "/sources", ""])
    row = participate(
        Runner(store, source, ScriptedWorker()), sid, read=lambda _: next(answers), write=output.append
    )
    assert row["state"] == before["state"] and row["revision"] == before["revision"]
    assert row["deadline"] == before["deadline"] and store.actions(sid) == actions
    assert source.evidence["trial"]["passage"] in "\n".join(output)
    assert "Source fixture:trial:" in "\n".join(output)


def test_context_pressure_preserves_sources_and_reserves_synthesis(source):
    import copy
    import json

    from cosmai.research.state import READ_ACTIONS, initial_state

    state = initial_state(Mission(objective="Finish within headroom", scope="controlled"))
    state["evidence"] = {
        f"large-{i}": Evidence(
            id=f"large-{i}",
            source="fixture",
            locator=f"fixture:{i}",
            captured_at="2026-11-04",
            passage="Controlled counterevidence " * 290,
        ).frozen()
        for i in range(6)
    }
    before = copy.deepcopy(state)

    class Retained:
        def get(self, sid):
            return {"state": copy.deepcopy(state), "revision": 7}

        def actions(self, sid):
            return []

        def remaining_seconds(self, sid):
            return 900

    context = Runner(cast(Store, Retained()), source, None).context("fixture", "request")
    resources = context["remaining_resources"]
    assert "context" in resources["pressure"]
    assert resources["context_characters"] == len(json.dumps(context))
    assert resources["context_remaining_characters"] == 150_000 - len(json.dumps(context))
    assert not READ_ACTIONS.intersection(context["available_actions"])
    assert {"assess", "finish", "integrate_feedback", "discuss"} <= context["available_actions"].keys()
    assert context["state"] == before == state
    assert len(json.dumps(context)) < 150_000


def test_call_and_source_pressure_are_visible_before_exhaustion(source):
    import copy

    from cosmai.research.state import initial_state

    state = initial_state(Mission(objective="Reserve completion calls", scope="controlled"))
    state["usage"].update(manager_calls=10, tool_calls=10)
    source = EvidenceSource(profile="bounded", bundle={"literature": {"max_requests": 12}, "evidence": []})

    class Retained:
        def get(self, sid):
            return {"state": copy.deepcopy(state), "revision": 2}

        def actions(self, sid):
            return [
                {
                    "id": str(i),
                    "kind": "literature_search",
                    "status": "done",
                    "result": {},
                    "observed_revision": i,
                }
                for i in range(11)
            ]

        def remaining_seconds(self, sid):
            return 100

    resources = Runner(cast(Store, Retained()), source, None).context("fixture", "request")[
        "remaining_resources"
    ]
    assert resources["synthesis_reserve_actions"] == 2
    assert set(resources["pressure"]) == {"calls", "time", "literature"}
    assert state["usage"]["manager_calls"] == 10


def test_fulltext_accepts_external_doctype_without_resolution(monkeypatch):
    from cosmai.research import literature
    from cosmai.research.fulltext import checked_sections

    xml = b'<!DOCTYPE article SYSTEM "https://invalid.example/jats.dtd">' + fulltext_xml()
    monkeypatch.setattr(literature, "fetch_xml", lambda _: xml)
    src = EvidenceSource(profile="public", bundle={"literature": {"max_requests": 12, "fulltext": True}})
    captured = src.execute("literature_fulltext", {"pmcid": "PMC123"}, fulltext_records())
    assert captured["document"]["xml"].encode() == xml
    assert "Original method" in checked_sections(captured)[0]["passage"]


@pytest.mark.postgres
def test_rejected_fulltext_survives_bounded_failure_and_restart(store, monkeypatch):
    import hashlib
    import json

    from cosmai.research import literature

    xml = b'<!DOCTYPE article [<!ENTITY x SYSTEM "file:///unavailable">]>' + fulltext_xml()
    monkeypatch.setattr(literature, "fetch_xml", lambda _: xml)
    monkeypatch.setattr(
        literature,
        "fetch",
        lambda _: {
            "resultList": {"result": fulltext_records()[0]["result"]["papers"]},
            "response_sha256": "controlled",
        },
    )
    src = EvidenceSource(profile="public", bundle={"literature": {"max_requests": 12, "fulltext": True}})
    sid = store.create(Mission(objective="Preserve failed source capture", scope="controlled"))
    worker = ScriptedWorker(
        ("literature_search", {"query": "controlled"}), ("literature_fulltext", {"pmcid": "PMC123"})
    )
    row = Runner(store, src, worker).run(sid, steps=2)
    assert row["state"]["usage"]["failures"] == 1
    assert row["state"]["usage"]["tool_calls"] == 2
    assert row["state"]["evidence"] == {}
    failed = next(a for a in Store(store.url).actions(sid) if a["kind"] == "literature_fulltext")
    assert failed["status"] == "failed"
    assert failed["result"]["document"]["xml"].encode() == xml
    assert failed["result"]["document"]["response_sha256"] == hashlib.sha256(xml).hexdigest()
    context = Runner(Store(store.url), src, None).context(sid, "restart")
    assert context["remaining_resources"]["literature_actions"] == 10
    assert "file:///unavailable" not in json.dumps(context)
    assert "rejected" in context["observations"][-1]["result"]["document_access"].lower()
