"""Read-only, versioned research procedures with an explicit evidence datasource."""

from __future__ import annotations

import json
from pathlib import Path

from analysis.discovery.core import select, validate_snapshot
from analysis.discovery.corroborate import corroborate_local
from analysis.discovery.sample import sample_local

from . import literature
from .state import ACTIONS, READ_ACTIONS, Evidence, fingerprint, resolve_citations


def snapshot_evidence(snapshot: dict) -> dict:
    validate_snapshot(snapshot)
    return {
        doc["doc_id"]: Evidence(
            id=doc["doc_id"],
            source=doc["source"],
            locator=doc.get("url") or doc["doc_id"],
            attribution=doc.get("author_hash"),
            captured_at=doc.get("collected_at") or snapshot["manifest"].get("finished_at", "unknown"),
            passage=doc["text"][:8000],
            limitations=[
                "Existing frozen source sample; no prevalence or market-absence inference",
                "Passage limited to first 8000 characters; inspect original for longer context",
            ],
        ).frozen()
        for doc in snapshot["documents"]
    }


class EvidenceSource:
    def __init__(self, *, profile: str, bundle: dict | None = None, database_url: str | None = None):
        if not profile or (bundle is None and not database_url):
            raise ValueError("explicit evidence bundle or database datasource required")
        self.profile = profile
        self.database_url = database_url
        self.bundle = bundle
        self.literature_limit = 0
        self.fulltext_enabled = False
        if bundle is not None and "literature" in bundle:
            config = bundle["literature"]
            if (
                not {"max_requests"} <= set(config) <= {"max_requests", "fulltext"}
                or type(config["max_requests"]) is not int
                or not 1 <= config["max_requests"] <= 12
                or type(config.get("fulltext", False)) is not bool
            ):
                raise ValueError("literature profile requires max_requests between 1 and 12")
            self.literature_limit = config["max_requests"]
            self.fulltext_enabled = config.get("fulltext", False)
        if bundle is not None:
            if "snapshot_sha256" in bundle:
                self.evidence = snapshot_evidence(bundle)
            else:
                records = [Evidence.model_validate(e).frozen() for e in bundle.get("evidence", [])]
                self.evidence = {e["id"]: e for e in records}
                if len(records) != len(self.evidence):
                    raise ValueError("duplicate evidence IDs")
        else:
            self.evidence = {}
        self.identity = {
            "profile": profile,
            "kind": "frozen" if bundle is not None else "database",
            "sha256": fingerprint(bundle) if bundle is not None else None,
        }

    @classmethod
    def from_file(cls, path: str, profile: str):
        file = Path(path)
        if file.stat().st_size > 2_000_000:
            raise ValueError("evidence bundle exceeds bounded import size")
        return cls(profile=profile, bundle=json.loads(file.read_text()))

    def schemas(self) -> dict:
        return {
            name: {
                "version": 2,
                "effect": "read" if name in READ_ACTIONS else "session_state",
                "arguments": model.model_json_schema(),
            }
            for name, model in ACTIONS.items()
            if (
                name not in {"sample", "analyze"}
                or self.database_url
                or self.bundle is not None
                and "snapshot_sha256" in self.bundle
            )
            and (name != "corroborate" or self.database_url)
            and (name not in literature.ACTIONS or self.literature_limit)
            and (name not in {"literature_fulltext", "literature_section"} or self.fulltext_enabled)
        }

    def execute(self, action: str, args: dict, records: list[dict]) -> dict:
        if action not in self.schemas():
            raise ValueError("capability unavailable for selected source profile")
        args = ACTIONS[action].model_validate(args).model_dump()
        available = dict(self.evidence)
        for record in records:
            if record["kind"] not in READ_ACTIONS or record["status"] not in {"done", "stale"}:
                continue
            for evidence in (record.get("result") or {}).get("evidence", []):
                available[evidence["id"]] = evidence
        provenance = {"procedure": f"{action}:v1", "source": self.identity, "inputs": args}
        if action in literature.ACTIONS:
            return {**provenance, **literature.execute(action, args, records, self.literature_limit)}
        if action == "search":
            if self.bundle is None and not available:
                raise ValueError("no captured evidence; use explicit sample first")
            words = args["query"].lower().split()
            ranked = sorted(
                available.values(),
                key=lambda e: (-sum(word in e["passage"].lower() for word in words), e["id"]),
            )
            selected = [e for e in ranked if any(w in e["passage"].lower() for w in words)][: args["limit"]]
            return {
                **provenance,
                "evidence": selected,
                "limitations": ["Literal search of this frozen pack only; no matches is not absence"],
            }
        if action == "read_evidence":
            if args["evidence_id"] not in available:
                raise ValueError("evidence is unavailable in selected source")
            return {**provenance, "evidence": [available[args["evidence_id"]]]}
        if action == "corroborate" and not self.database_url:
            raise ValueError("explicit database evidence datasource required")
        if action == "sample":
            snapshot = (
                self.bundle
                if self.bundle is not None and "snapshot_sha256" in self.bundle
                else sample_local(args["per_cue"], database_url=self.database_url)
            )
            return {
                **provenance,
                "snapshot": snapshot,
                "evidence": list(snapshot_evidence(snapshot).values()),
            }
        record = next(
            (r for r in records if r["id"] == args["sample_action"] and r["status"] == "done"), None
        )
        if not record or "snapshot" not in (record["result"] or {}):
            raise ValueError("completed sample execution required")
        snapshot = record["result"]["snapshot"]
        review = resolve_review(args["review"], snapshot, available)
        if action == "corroborate":
            queries = args["queries"]
            for group in queries["groups"].values():
                for anchor in group["anchors"]:
                    anchor["span"] = extract_span(
                        anchor["doc_id"], anchor.pop("span_id"), snapshot_evidence(snapshot)
                    )
            snapshot = corroborate_local(
                record["result"]["snapshot"],
                review,
                queries,
                args["per_query"],
                database_url=self.database_url,
            )
            return {
                **provenance,
                "snapshot": snapshot,
                "evidence": list(snapshot_evidence(snapshot).values()),
            }
        if action == "analyze":
            return {
                **provenance,
                "selection": select(snapshot, review),
                "limitations": ["Historical discovery procedure, not universal research criteria"],
            }
        raise ValueError("unsupported research tool")


def extract_span(doc_id, span_id, evidence):
    return resolve_citations([{"evidence_id": doc_id, "span_id": span_id}], evidence)[0]["passage"]


def resolve_review(review, snapshot, available):
    import copy

    review = copy.deepcopy(review)
    evidence = snapshot_evidence(snapshot)
    for observation in review["observations"]:
        doc_id = observation["doc_id"]
        for field in ("context", "requirement", "behavior", "burden", "trade_off"):
            claim = observation.get(field)
            if claim:
                claim["span"] = extract_span(doc_id, claim.pop("span_id"), evidence)
            else:
                observation.pop(field, None)
        for field in ("span", "recurrence_span"):
            span_id = observation.pop(field + "_id", None)
            if span_id:
                observation[field] = extract_span(doc_id, span_id, evidence)
    for candidate in review["candidates"].values():
        candidate["technical"]["references"] = resolve_citations(
            candidate["technical"]["references"], available
        )
    return review


def _tool_child(pipe, source, action, args, records):
    try:
        result = source.execute(action, args, records)
        if len(json.dumps(result)) > 2_000_000:
            raise ValueError("tool result exceeds artifact limit")
        pipe.send((True, result))
    except Exception as exc:
        from .fulltext import CaptureFailure
        from .runner import validation_error

        if isinstance(exc, CaptureFailure):
            payload = {"details": validation_error(exc), "artifact": exc.artifact}
            if len(json.dumps(payload)) <= 2_000_000:
                pipe.send((False, payload))
            else:
                pipe.send((False, "rejected capture exceeds artifact limit; body unavailable"))
        else:
            pipe.send((False, validation_error(exc)))
    finally:
        pipe.close()


def bounded_read(source, action, args, records, timeout):
    """A read's entire procedure, including repeated DB queries, has a wall-clock bound."""
    import multiprocessing

    context = multiprocessing.get_context("fork")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_tool_child, args=(child, source, action, args, records))
    process.start()
    child.close()
    try:
        if not parent.poll(timeout):
            raise TimeoutError("read procedure exceeded its allowance")
        ok, value = parent.recv()
        if not ok:
            if isinstance(value, dict) and "artifact" in value:
                from .fulltext import CaptureFailure

                raise CaptureFailure(value["details"], value["artifact"])
            raise ValueError(str(value))
        return value
    finally:
        parent.close()
        if process.is_alive():
            process.terminate()
        process.join(timeout=2)
        if process.is_alive():
            process.kill()
            process.join()
