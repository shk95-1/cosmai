"""Versioned research records and capability contracts; no provider-specific reasoning."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    span_id: Key


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


class GroundedClaim(Record):
    summary: Text
    span_id: Key


class DiscoveryObservation(Record):
    doc_id: Key
    disposition: Literal["observed", "wish", "promotion", "ambiguous", "satisfied", "off_topic"]
    reason: Text
    candidate: Key | None = None
    context: GroundedClaim | None = None
    requirement: GroundedClaim | None = None
    behavior: GroundedClaim | None = None
    burden: GroundedClaim | None = None
    trade_off: GroundedClaim | None = None
    recurring: bool = False
    recurrence_span_id: Key | None = None
    span_id: Key | None = None

    @model_validator(mode="after")
    def grounded_fields(self):
        if self.disposition == "observed":
            for field in ("context", "requirement", "behavior", "burden", "trade_off", "candidate"):
                if getattr(self, field) is None:
                    raise ValueError(f"observed disposition requires {field}")
            if self.recurring and self.recurrence_span_id is None:
                raise ValueError("recurring observations require recurrence_span_id")
        elif self.span_id is None:
            raise ValueError("non-observed disposition requires span_id")
        return self


class Technical(Record):
    status: Literal["unassessed", "available", "contradictory"]
    reason: Text
    references: list[Citation] = Field(default_factory=list)


class Feasibility(Record):
    status: Literal["unassessed", "existing_process_hypothesis", "incompatible"]
    reason: Text


class Candidate(Record):
    need: Text
    limitations: Text
    technical: Technical
    feasibility: Feasibility


class DiscoveryReview(Record):
    snapshot_sha256: Key
    reviewer: Text
    interventions: list[Text]
    observations: list[DiscoveryObservation]
    candidates: dict[str, Candidate]


class QueryAnchor(Record):
    doc_id: Key
    span_id: Key


class RequirementQuery(Record):
    reason: Text
    anchors: list[QueryAnchor] = Field(min_length=1)
    all_of: list[
        Annotated[list[Annotated[str, Field(min_length=1, max_length=40)]], Field(min_length=1, max_length=8)]
    ] = Field(min_length=1, max_length=4)


class DiscoveryQueries(Record):
    snapshot_sha256: Key
    reviewer: Text
    groups: dict[str, RequirementQuery]


class Corroborate(Record):
    sample_action: Key
    review: DiscoveryReview
    queries: DiscoveryQueries
    per_query: int = Field(default=1, ge=1, le=3)


class Analyze(Record):
    sample_action: Key
    review: DiscoveryReview


class LiteratureSearch(Record):
    query: Annotated[str, Field(min_length=1, max_length=500)]
    limit: int = Field(default=5, ge=1, le=8)


class LiteratureRead(Record):
    source: Literal["MED", "PMC"]
    article_id: Annotated[str, Field(pattern=r"^(?:PMC)?[0-9]{1,12}$")]


class Contribution(Record):
    feedback_id: Key
    kind: Literal["information", "idea", "decision", "goal_change"]
    interpretation: Text
    response: Text
    discussion_id: Key | None = None
    affected_tasks: list[Key] = Field(default_factory=list)
    affected_hypotheses: list[Key] = Field(default_factory=list)
    affected_discussions: list[Key] = Field(default_factory=list)
    objective: Text | None = None
    preferences: list[Text] | None = None


class DevelopmentRequest(Record):
    capability: Text
    reason: Text
    blocked_question: Text


class Pause(Record):
    reason: Text


ACTIONS = {
    "search": Search,
    "literature_search": LiteratureSearch,
    "literature_read": LiteratureRead,
    "integrate_feedback": Contribution,
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
READ_ACTIONS = {
    "search",
    "read_evidence",
    "sample",
    "corroborate",
    "analyze",
    "literature_search",
    "literature_read",
}


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


def evidence_spans(evidence: dict) -> list[dict]:
    """Offsets address original codepoints; never normalize or regenerate source text."""
    frozen = Evidence.model_validate(evidence).frozen()
    text = frozen["passage"]
    spans = []
    # Sentence/newline boundaries, with a finite fallback for long unpunctuated passages.
    start = 0
    for match in re.finditer(r"(?<=[.!?])\s+|\n+|$", text):
        end = match.start()
        while start < end:
            stop = min(start + 600, end)
            spans.append(
                {
                    "id": f"{frozen['sha256'][:24]}:{start}:{stop}",
                    "start": start,
                    "end": stop,
                    "passage": text[start:stop],
                }
            )
            start = stop
        start = match.end()
    return spans


def resolve_citations(citations: list[dict], evidence: dict) -> list[dict]:
    resolved = []
    for citation in citations:
        ref = Citation.model_validate(citation)
        if ref.evidence_id not in evidence:
            raise ValueError("unknown evidence_id; read the source before citing it")
        item = evidence[ref.evidence_id]
        span = next((s for s in evidence_spans(item) if s["id"] == ref.span_id), None)
        if span is None:
            raise ValueError("unknown or changed span_id; select an ID from evidence_spans")
        resolved.append({**ref.model_dump(), "passage": span["passage"], "sha256": item["sha256"]})
    return resolved


def validate_citations(citations: list[dict], evidence: dict) -> None:
    """Validate persisted legacy quotations and resolved citations; new inputs use span IDs."""
    for ref in citations:
        if "span_id" in ref:
            resolved = resolve_citations([{k: ref[k] for k in ("evidence_id", "span_id")}], evidence)[0]
            if ref.get("passage", resolved["passage"]) != resolved["passage"]:
                raise ValueError("changed extracted passage")
        elif (
            ref.get("evidence_id") not in evidence
            or not ref.get("passage")
            or ref["passage"] not in evidence[ref["evidence_id"]]["passage"]
        ):
            raise ValueError("citation must name an available exact evidence passage")


def resolve_argument_citations(value, evidence: dict):
    if isinstance(value, list):
        return [resolve_argument_citations(v, evidence) for v in value]
    if isinstance(value, dict):
        if "evidence_id" in value and "span_id" in value:
            return resolve_citations([value], evidence)[0]
        return {k: resolve_argument_citations(v, evidence) for k, v in value.items()}
    return value


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
