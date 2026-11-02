"""Versioned research records and capability contracts; no provider-specific reasoning."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Text = Annotated[str, Field(min_length=1, max_length=8000)]
Key = Annotated[str, Field(min_length=1, max_length=160)]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Limits(Record):
    manager_calls: int = Field(default=12, ge=1, le=100)
    tool_calls: int = Field(default=12, ge=1, le=100)
    seconds: int = Field(default=1200, ge=1, le=7200)
    call_seconds: int = Field(default=90, ge=1, le=180)
    failures: int = Field(default=2, ge=1, le=5)


class Mission(Record):
    objective: Text
    scope: Text
    preferences: list[Text] = Field(default_factory=list, max_length=30)
    unresolved_preferences: list[Text] = Field(default_factory=list, max_length=30)
    success: Text = "Qualified source-grounded findings and useful next validation."
    stopping: Text = "Stop at evidence/resource limit or a blocking participant decision."
    limits: Limits = Field(default_factory=Limits)
    paid_budget: Literal[0] = 0


class Evidence(Record):
    id: Key
    source: Text
    locator: Text
    attribution: str | None = None
    captured_at: Text
    passage: Text
    limitations: list[Text] = Field(default_factory=list)
    sha256: str = ""

    def frozen(self) -> dict:
        data = self.model_dump(exclude={"sha256"})
        digest = fingerprint(data)
        if self.sha256 and self.sha256 != digest:
            raise ValueError("evidence hash mismatch")
        return {**data, "sha256": digest}


class Citation(Record):
    evidence_id: Key
    passage: Text


class Hypothesis(Record):
    id: Key
    problem: Text
    affected_users: Text
    opportunity: Text
    support: list[Citation] = Field(default_factory=list)
    counterevidence: list[Citation] = Field(default_factory=list)
    uncertainties: list[Text]
    status: Literal["proposed", "supported", "held", "rejected"]
    reason: Text
    next_question: Text


class Task(Record):
    id: Key
    question: Text
    information_gain: Text
    method: Text
    dependencies: list[Key] = Field(default_factory=list)
    status: Literal["ready", "blocked", "done", "superseded"] = "ready"
    result_refs: list[Key] = Field(default_factory=list)


class Discussion(Record):
    id: Key
    question: Text
    why_now: Text
    finding: Text
    evidence: list[Citation]
    uncertainty: Text
    options: list[Text] = Field(min_length=2, max_length=5)
    recommendation: Text
    strongest_objection: Text
    contribution: Text
    dependent_tasks: list[Key]
    independent_work: Text
    authorized_default: str | None = None


class Assessment(Record):
    summary: Text
    hypotheses: list[Hypothesis] = Field(default_factory=list, max_length=30)
    tasks: list[Task] = Field(default_factory=list, max_length=30)
    criteria: Text
    criteria_reason: Text
    effect_on_prior_assessments: Text


class Finding(Record):
    summary: Text
    evidence: list[Citation]
    uncertainties: list[Text]
    next_validation: Text
    outcome: Literal["findings", "evidence_limit", "participant_input_required"]


class Search(Record):
    query: Text
    limit: int = Field(default=5, ge=1, le=20)


class ReadEvidence(Record):
    evidence_id: Key


class Sample(Record):
    per_cue: int = Field(default=1, ge=1, le=3)


class Corroborate(Record):
    sample_action: Key
    review: dict
    queries: dict
    per_query: int = Field(default=1, ge=1, le=3)


class Analyze(Record):
    sample_action: Key
    review: dict


class DevelopmentRequest(Record):
    capability: Text
    reason: Text
    blocked_question: Text


class Pause(Record):
    reason: Text


ACTIONS = {
    "search": Search,
    "read_evidence": ReadEvidence,
    "sample": Sample,
    "corroborate": Corroborate,
    "analyze": Analyze,
    "assess": Assessment,
    "discuss": Discussion,
    "finish": Finding,
    "pause": Pause,
    "request_capability": DevelopmentRequest,
}
READ_ACTIONS = {"search", "read_evidence", "sample", "corroborate", "analyze"}


class Proposal(Record):
    request_id: Key
    observed_revision: int = Field(ge=0)
    goal_revision: int = Field(ge=0)
    action: str
    arguments: dict
    rationale: Text
    question: Text
    expected_result: Text
    changed: Text
    evidence: list[Citation] = Field(default_factory=list)
    task_id: str | None = None


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def validate_citations(citations: list[dict], evidence: dict) -> None:
    for citation in citations:
        ref = Citation.model_validate(citation)
        if ref.evidence_id not in evidence or ref.passage not in evidence[ref.evidence_id]["passage"]:
            raise ValueError("citation must name an available exact evidence passage")


def initial_state(mission: Mission) -> dict:
    return {
        "version": 1,
        "mission": mission.model_dump(),
        "goal_revision": 0,
        "status": "ready",
        "outcome": None,
        "reason": "Session created",
        "summary": "No findings yet",
        "hypotheses": {},
        "tasks": {},
        "discussions": {},
        "evidence": {},
        "findings": [],
        "feedback": [],
        "criteria_history": [],
        "development_requests": [],
        "observations": [],
        "usage": {"manager_calls": 0, "tool_calls": 0, "failures": 0, "uncertain": 0},
    }
