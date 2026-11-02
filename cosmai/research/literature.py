"""Bounded Europe PMC metadata/abstract access; fixed host, no redirects or hidden retries."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import httpx

from .state import Evidence

ENDPOINT = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
ACTIONS = {"literature_search", "literature_read"}


def fetch(params: dict) -> dict:
    with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
        with client.stream(
            "GET",
            ENDPOINT,
            params={**params, "format": "json"},
            headers={"User-Agent": "COSMAI-research/1.0 (bounded literature research)"},
        ) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > 1_000_000:
                    raise ValueError("literature response exceeds 1 MB")
    import json

    result = json.loads(data)
    result["response_sha256"] = hashlib.sha256(data).hexdigest()
    return result


def execute(action: str, args: dict, records: list[dict], limit: int) -> dict:
    # The current reservation is already present; failed/stale/uncertain attempts also consume quota.
    used = sum(r["kind"] in ACTIONS for r in records)
    if used > limit:
        raise ValueError("literature request allowance exhausted; no request sent")
    if action == "literature_search":
        response = fetch({"query": args["query"], "resultType": "lite", "pageSize": args["limit"]})
        papers = [
            {k: p.get(k) for k in ("id", "source", "title", "authorString", "pubYear", "doi")}
            for p in response.get("resultList", {}).get("result", [])
            if p.get("source") in {"MED", "PMC"}
        ]
        return {
            "papers": papers,
            "hit_count": response.get("hitCount"),
            "response_sha256": response["response_sha256"],
            "limitations": [
                "Relevance-ranked finite literature search; titles are not findings",
                "Publication and indexing bias; no market-demand evidence",
            ],
        }
    known = {
        (p["source"], p["id"])
        for r in records
        if r["kind"] == "literature_search" and r["status"] in {"done", "stale"}
        for p in (r.get("result") or {}).get("papers", [])
    }
    key = (args["source"], args["article_id"])
    if key not in known:
        raise ValueError("article must be discovered by literature_search in this session")
    for record in records:
        if record["kind"] == "literature_read" and record["status"] in {"done", "stale"}:
            result = record.get("result") or {}
            article = result.get("article", {})
            if (article.get("source"), article.get("id")) == key:
                return {
                    k: result[k] for k in ("evidence", "article", "response_sha256", "abstract_characters")
                }
    response = fetch({"query": f"EXT_ID:{key[1]} AND SRC:{key[0]}", "resultType": "core", "pageSize": 1})
    papers = response.get("resultList", {}).get("result", [])
    if not papers or (papers[0].get("source"), papers[0].get("id")) != key:
        raise ValueError("requested article not returned; no negative research inference")
    paper = papers[0]
    abstract = paper.get("abstractText")
    if not abstract:
        raise ValueError("abstract unavailable; do not infer findings from title")
    # Preserve API text exactly, including any inline markup. Never normalize a quotation.
    evidence = Evidence(
        id=f"epmc:{key[0]}:{key[1]}",
        source="Europe PMC article abstract",
        locator=f"https://europepmc.org/article/{key[0]}/{key[1]}",
        attribution=paper.get("authorString"),
        captured_at=datetime.now(UTC).isoformat(),
        passage=abstract[:8000],
        limitations=[
            "Abstract only, not full methods or independent replication",
            "No inference of consumer demand or manufacturing feasibility without evidence",
            "Original API markup preserved; abstract truncated at 8000 characters if longer",
        ],
    ).frozen()
    return {
        "evidence": [evidence],
        "article": {
            k: paper.get(k)
            for k in ("id", "source", "title", "pubYear", "doi", "pubTypeList", "authorString")
        },
        "response_sha256": response["response_sha256"],
        "abstract_characters": len(abstract),
    }
