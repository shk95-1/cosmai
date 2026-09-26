"""Bind case-specific technical evidence without upgrading unresolved market claims."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from urllib.parse import urlsplit

from analysis.discovery.core import fingerprint, select


def validate_technical(sample: dict, review: dict, assessment: dict) -> dict:
    """Return the original candidate; validate provenance, not scientific truth or approval.

    A user-adjudicated route is separate from the unchanged automatic market gate. The
    caller still reviews the real decision ledger and source applicability. This offline
    check does not certify an HTTPS page, the user's identity or a product's performance.
    """
    name = assessment["candidate"]
    result = select(sample, review)
    candidate = next(c for c in result["candidates"] if c["candidate"] == name)
    market = review["market_reviews"][name]
    if (
        assessment.get("version") != "technical-v1"
        or assessment.get("snapshot_sha256") != sample["snapshot_sha256"]
        or assessment.get("support_sha256") != fingerprint(candidate["support"])
        or assessment.get("market_sha256") != fingerprint(market)
    ):
        raise ValueError("technical review targets different source or comparison evidence")
    prior = deepcopy(review)
    prior["market_reviews"].pop(name)
    if select(sample, prior)["selected"] != name:
        raise ValueError("technical candidate must be the recorded pre-comparison automatic selection")
    release = assessment["release"]
    if release.get("route") != "user_provisional_adjudication" or not release.get("rationale"):
        raise ValueError("an explicit provisional user decision is required")
    if urlsplit(release.get("decision_url", "")).scheme != "https":
        raise ValueError("user decision ledger URL required")
    if datetime.fromisoformat(release["recorded_at"]).tzinfo is None:
        raise ValueError("user decision needs a timezone")
    if release.get("automatic_market_disposition") != {k: market["decision"][k] for k in ("status", "basis")}:
        raise ValueError("user adjudication must preserve the automatic market disposition")
    sources = assessment["sources"]
    ids = {s["id"] for s in sources}
    if not sources or len(ids) != len(sources):
        raise ValueError("unique technical source IDs required")
    for source in sources:
        if source.get("kind") not in {"supplier_specification", "patent_specification", "primary_research"}:
            raise ValueError("technical source kind must be explicit")
        if source.get("access") not in {"body", "abstract_only", "unavailable"}:
            raise ValueError("technical access scope required")
        if urlsplit(source.get("url", "")).scheme != "https":
            raise ValueError("technical source URL required")
        date.fromisoformat(source["retrieved_on"])
        if not all(source.get(k) for k in ("title", "publisher", "locator", "conditions", "limitations")):
            raise ValueError("technical source provenance and applicability limits required")
        if source["access"] != "unavailable" and not source.get("passage"):
            raise ValueError("accessible technical source needs a supporting passage")
    if not assessment["claims"] or not assessment["proposed_tests"]:
        raise ValueError("technical handoff needs claims and proposed tests")
    for claim in assessment["claims"]:
        references = claim["source_ids"]
        if not references or not set(references) <= ids:
            raise ValueError("technical claim references an unknown source")
        if not all(
            claim.get(k)
            for k in ("statement", "material_or_component", "conditions", "applicability", "limits")
        ):
            raise ValueError("technical claim needs conditions and case applicability")
        if claim.get("basis") not in {"source_statement", "agent_inference"}:
            raise ValueError("technical claim must distinguish inference")
        if any(s["access"] == "unavailable" for s in sources if s["id"] in references):
            raise ValueError("inaccessible evidence cannot support a decisive technical claim")
    for test in assessment["proposed_tests"]:
        if test.get("state") != "proposed_not_run" or "results" in test:
            raise ValueError("proposed tests must not carry invented results")
        if not all(test.get(k) for k in ("objective", "control", "measure", "limitations")):
            raise ValueError("proposed test needs a control and measurement limits")
    if assessment["disposition"].get("status") != "conditional_basis_for_brief":
        raise ValueError("technical evidence does not certify a finished product or accepted case")
    return candidate
