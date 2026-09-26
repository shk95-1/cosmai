"""Case-level product comparisons and explicit feedback, without catalogue-name linking.

The agent records the comparison; these checks bind it to the reviewed observations and
refuse common identity/unknown-as-gap errors. They do not verify a brand's performance.
"""

from __future__ import annotations

from datetime import date
from urllib.parse import urlsplit


def identity_relation(left: dict, right: dict) -> str:
    """Source keys identify a line/listing; only an explicit option identifies a variant.

    Listing containment must never become equality or a transitive cross-site identity.
    Known conflicting SPF, scent, shade or generation always defeats variant equality.
    """
    for product in (left, right):
        if product.get("level") not in {"line", "listing", "variant"}:
            raise ValueError("identity needs line/listing/variant level")
        if not product.get("source") or not product.get("product_id"):
            raise ValueError("source product key required")
        if product["level"] == "variant" and not product.get("variant_key"):
            raise ValueError("variant needs an explicit option/SKU key")
    a, b = left.get("attributes", {}), right.get("attributes", {})
    if any(a[k] != b[k] for k in a.keys() & b.keys() if a[k] is not None and b[k] is not None):
        return "distinct"
    if (left["source"], left["product_id"]) != (right["source"], right["product_id"]):
        return "unresolved"
    if left["level"] == right["level"]:
        if left["level"] == "variant":
            return "same_variant" if left["variant_key"] == right["variant_key"] else "distinct"
        return f"same_{left['level']}"
    listing, variant = (left, right) if left["level"] == "listing" else (right, left)
    if listing["level"] == "listing" and variant["level"] == "variant":
        if variant["variant_key"] in listing.get("variant_keys", []):
            return "listing_contains_variant"
    return "unresolved"


def _reference(evidence: dict) -> None:
    if evidence.get("kind") not in {"manufacturer_claim", "user_self_report", "agent_inference"}:
        raise ValueError("comparison evidence must identify its kind")
    if urlsplit(evidence.get("url", "")).scheme != "https" or not evidence.get("summary"):
        raise ValueError("comparison needs an HTTPS source and explicit claim")
    date.fromisoformat(evidence["retrieved_on"])


def market_reason(sample: dict, candidate: dict, assessment: dict) -> str | None:
    """Validate the full matrix before allowing it to exclude a discovery candidate.

    A retained lead is still provisional. Unknown suitability or stock never establishes
    market absence. Semantic interpretation and the recorded decision need human review.
    """
    from analysis.discovery.core import fingerprint

    if (
        assessment.get("version") != "alternatives-v1"
        or assessment.get("snapshot_sha256") != sample["snapshot_sha256"]
        or assessment.get("candidate") != candidate["candidate"]
        or assessment.get("support_sha256") != fingerprint(candidate["support"])
    ):
        raise ValueError("market review targets different source evidence")
    docs = {d["doc_id"]: d for d in sample["documents"]}
    support = {r["doc_id"] for r in candidate["support"]}
    requirements = assessment["requirements"]
    ids = {r["id"] for r in requirements}
    if not ids or len(ids) != len(requirements) or {r["doc_id"] for r in requirements} != support:
        raise ValueError("requirements must cover every supporting observation once or more")
    for r in requirements:
        if not r.get("span") or r["span"] not in docs[r["doc_id"]]["text"] or not r.get("interpretation"):
            raise ValueError("requirement needs its original evidence span")
        if r.get("priority") not in {"must_have", "preferred", "unknown"}:
            raise ValueError("requirement priority must be explicit")
        if type(r.get("variant_specific")) is not bool or not isinstance(r.get("unknowns"), list):
            raise ValueError("variant scope and unknown requirements must be explicit")
    alternatives = assessment["alternatives"]
    if not alternatives or not assessment.get("coverage") or not assessment.get("limitations"):
        raise ValueError("alternatives, bounded coverage and limitations required")
    for alternative in alternatives:
        product = alternative["identity"]
        identity_relation(product, product)  # also validates an unresolved line/listing
        _reference(alternative["source"])
        if not alternative.get("availability"):
            raise ValueError("availability uncertainty required")
        comparisons = alternative["comparisons"]
        if len(comparisons) != len(ids) or {c["requirement"] for c in comparisons} != ids:
            raise ValueError("each alternative must compare every requirement exactly once")
        for c in comparisons:
            if c.get("status") not in {"met", "contradicted", "unknown"} or not c.get("reason"):
                raise ValueError("comparison status and reasoning required")
            if c.get("evidence"):
                _reference(c["evidence"])
            elif c["status"] != "unknown":
                raise ValueError("met/contradicted needs source evidence")
            requirement = next(r for r in requirements if r["id"] == c["requirement"])
            if c["status"] == "met" and requirement["variant_specific"] and product["level"] != "variant":
                raise ValueError("a product line/listing cannot satisfy a variant-specific requirement")
    decision = assessment["decision"]
    if decision.get("status") not in {"reject", "retain_for_technical_review"}:
        raise ValueError("explicit market disposition required")
    if not decision.get("reason") or not decision.get("falsifier"):
        raise ValueError("market decision needs its reason and falsifier")
    if decision["status"] == "retain_for_technical_review":
        # An actual contradiction is a minimum for a claimed residual gap in this bounded
        # comparison. It still proves neither worldwide absence nor technical feasibility.
        must_have = {r["id"] for r in requirements if r["priority"] == "must_have"}
        if not must_have or any(
            not any(c["requirement"] in must_have and c["status"] == "contradicted" for c in a["comparisons"])
            for a in alternatives
        ):
            raise ValueError("unknown suitability alone cannot establish a residual gap")
        return None
    if decision.get("basis") not in {
        "insufficient_common_requirement",
        "existing_alternative",
        "insufficient_alternative_evidence",
    }:
        raise ValueError("rejection basis required")
    if decision["basis"] == "insufficient_alternative_evidence":
        required = {r["id"] for r in requirements if r["priority"] == "must_have"}
        if not required or not any(
            c["requirement"] in required and c["status"] == "unknown"
            for a in alternatives
            for c in a["comparisons"]
        ):
            raise ValueError("insufficient alternatives evidence needs an unresolved must-have")
    if decision["basis"] == "existing_alternative":
        required = {r["id"] for r in requirements if r["priority"] == "must_have"}
        if not required or not any(
            all(
                c["status"] == "met" and c.get("evidence", {}).get("kind") == "user_self_report"
                for c in a["comparisons"]
                if c["requirement"] in required
            )
            for a in alternatives
        ):
            raise ValueError("brand claims alone cannot establish a satisfactory alternative")
    return f"market review ({decision['basis']}): {decision['reason']}"
