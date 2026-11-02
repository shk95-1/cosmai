"""Immutable fulltext captures and selected XML text-node passages."""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

from .state import Evidence, fingerprint


def sections(document):
    xml = document["xml"]
    if "<!DOCTYPE" in xml or "<!ENTITY" in xml:
        raise ValueError("XML DTD/entity declarations are unsupported")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError("invalid fulltext XML") from exc
    identifiers = {
        (n.text or "") if (n.text or "").startswith("PMC") else "PMC" + (n.text or "")
        for n in root.findall("./front/article-meta/article-id")
        if n.get("pub-id-type") in {"pmcid", "pmc"}
    }
    if root.tag != "article" or document["pmcid"] not in identifiers:
        raise ValueError("fulltext article identifier does not match requested PMC article")
    fragments = []
    for container in ("body", "back"):
        for ordinal, element in enumerate(root.findall(f"./{container}/*")):
            text = "".join(element.itertext())
            if not text.strip():
                continue
            title = element.find("title")
            heading = "".join(title.itertext()) if title is not None else element.tag
            fragments.append((container, ordinal, heading, "text_nodes", text))
    # Cell boundaries and spanning headers cannot be reconstructed from concatenated table text.
    for ordinal, table in enumerate(root.findall(".//table-wrap")):
        title = " ".join("".join(n.itertext()) for n in table if n.tag in {"label", "caption"})
        fragments.append(
            ("table", ordinal, title or "Table", "table_xml", ET.tostring(table, encoding="unicode"))
        )
    found = []
    for container, ordinal, heading, representation, text in fragments:
        for start in range(0, len(text), 8000):
            end = min(start + 8000, len(text))
            found.append(
                {
                    "id": f"{document['response_sha256'][:24]}:{container}:{ordinal}:{start}:{end}",
                    "title": heading[:200],
                    "container": container,
                    "representation": representation,
                    "start": start,
                    "end": end,
                    "section_characters": len(text),
                    "passage": text[start:end],
                }
            )
    if not found:
        raise ValueError("no body/back text available; do not infer absence of findings")
    return found


def index(parts):
    return [{k: v for k, v in part.items() if k != "passage"} for part in parts]


def checked_sections(result):
    document = result["document"]
    if fingerprint(document) != result["document_sha256"] or (
        hashlib.sha256(document["xml"].encode("utf-8")).hexdigest() != document["response_sha256"]
    ):
        raise ValueError("fulltext capture changed")
    parts = sections(document)
    if index(parts) != result["sections"]:
        raise ValueError("fulltext section index changed")
    return parts


def execute(action, args, records):
    if action == "literature_section":
        record = next(
            (
                r
                for r in records
                if r.get("id") == args["fulltext_action"]
                and r["kind"] == "literature_fulltext"
                and r["status"] in {"done", "stale"}
            ),
            None,
        )
        if record is None:
            raise ValueError("completed fulltext capture required")
        result = record["result"]
        part = next((p for p in checked_sections(result) if p["id"] == args["section_id"]), None)
        if part is None:
            raise ValueError("unknown section_id; select an ID from this capture's sections")
        document = result["document"]
        evidence = Evidence(
            id=f"epmc:{document['pmcid']}:{part['id']}",
            source="Europe PMC fulltext " + part["representation"],
            locator=f"https://europepmc.org/article/PMC/{document['pmcid'].removeprefix('PMC')}",
            attribution=document["article"].get("authorString"),
            captured_at=document["captured_at"],
            passage=part["passage"],
            limitations=[
                f"Selected {part['title']} text at {part['start']}:{part['end']} of "
                f"{part['section_characters']} characters; other indexed sections are not read",
                "Text-node representation preserves Unicode without inserted whitespace; table_xml "
                "is XML serialization from the same immutable capture, preserving cell/header structure",
                "Tables in text-node sections are linearized; select indexed table_xml for relationships",
                "Source statements require applicability and bias assessment; not independent replication",
            ],
        ).frozen()
        return {
            "network_requests": 0,
            "evidence": [evidence],
            "section": {k: v for k, v in part.items() if k != "passage"},
            "document_sha256": result["document_sha256"],
            "response_sha256": document["response_sha256"],
            "fulltext_action": record["id"],
        }
    known = [
        p
        for r in records
        if r["kind"] == "literature_search" and r["status"] in {"done", "stale"}
        for p in (r.get("result") or {}).get("papers", [])
        if p.get("pmcid") == args["pmcid"]
    ]
    if not known:
        raise ValueError("PMC article must be discovered by literature_search in this session")
    eligible = [p for p in known if str(p.get("isOpenAccess", "")).upper() == "Y"]
    if not eligible:
        raise ValueError("discovered article is not eligible for open-access fulltext")
    for record in records:
        if record["kind"] == "literature_fulltext" and record["status"] in {"done", "stale"}:
            result = record.get("result") or {}
            if result.get("document", {}).get("pmcid") == args["pmcid"]:
                checked_sections(result)
                return {
                    **{k: result[k] for k in ("document", "document_sha256", "sections", "limitations")},
                    "network_requests": 0,
                }
    from .literature import fetch_xml

    data = fetch_xml(args["pmcid"])
    try:
        xml = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("fulltext XML must be UTF-8") from exc
    document = {
        "pmcid": args["pmcid"],
        "xml": xml,
        "response_sha256": hashlib.sha256(data).hexdigest(),
        "captured_at": datetime.now(UTC).isoformat(),
        "article": eligible[0],
    }
    return {
        "network_requests": 1,
        "document": document,
        "document_sha256": fingerprint(document),
        "sections": index(sections(document)),
        "limitations": [
            "Index only: select literature_section to inspect content before making claims",
            "Body/back text indexed in original order; tables also indexed as structural XML fragments",
            "Chunks are at most 8000 characters; all chunks remain indexed without silent truncation",
            "No figure images or supplementary files; access failure does not establish absence",
        ],
    }


def context_result(action, result, evidence=None):
    if not result:
        return result
    projected = dict(result)
    if action == "literature_fulltext" and "document" in projected:
        projected.pop("document")
        projected["document_access"] = (
            "Original XML is retained in this execution artifact. Use literature_section "
            "with this execution_id as fulltext_action and a section_id from sections to read it."
        )
    observed = projected.get("evidence")
    if observed and evidence is not None and all(evidence.get(e["id"]) == e for e in observed):
        # Reference only byte-for-byte equal records already present in this very context.
        # Unknown or different passages remain visible; durable action results are never mutated.
        projected.pop("evidence")
        projected["evidence_refs"] = [{"evidence_id": e["id"], "sha256": e["sha256"]} for e in observed]
        projected["evidence_access"] = (
            "Identical original passages and provenance are available in state.evidence by evidence_id; "
            "select citation spans from evidence_spans. The durable observation retains the full records."
        )
    return projected
