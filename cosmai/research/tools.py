"""Read-only, versioned research procedures with an explicit evidence datasource."""

from __future__ import annotations

import json
from pathlib import Path

from analysis.discovery.core import select, validate_snapshot
from analysis.discovery.corroborate import corroborate_local
from analysis.discovery.sample import sample_local

from .state import ACTIONS, READ_ACTIONS, Evidence, fingerprint


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
        if bundle is not None:
            if "snapshot_sha256" in bundle:
                self.evidence = snapshot_evidence(bundle)
            else:
                records = [Evidence.model_validate(e).frozen() for e in bundle["evidence"]]
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
                "version": 1,
                "effect": "read" if name in READ_ACTIONS else "session_state",
                "arguments": model.model_json_schema(),
            }
            for name, model in ACTIONS.items()
            if self.database_url or name not in {"sample", "corroborate"}
        }

    def execute(self, action: str, args: dict, records: list[dict]) -> dict:
        args = ACTIONS[action].model_validate(args).model_dump()
        available = dict(self.evidence)
        for record in records:
            if record["kind"] not in READ_ACTIONS or record["status"] not in {"done", "stale"}:
                continue
            for evidence in (record.get("result") or {}).get("evidence", []):
                available[evidence["id"]] = evidence
        provenance = {"procedure": f"{action}:v1", "source": self.identity, "inputs": args}
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
        if action in {"sample", "corroborate"} and not self.database_url:
            raise ValueError("explicit database evidence datasource required")
        if action == "sample":
            snapshot = sample_local(args["per_cue"], database_url=self.database_url)
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
        if action == "corroborate":
            snapshot = corroborate_local(
                record["result"]["snapshot"],
                args["review"],
                args["queries"],
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
                "selection": select(record["result"]["snapshot"], args["review"]),
                "limitations": ["Historical discovery procedure, not universal research criteria"],
            }
        raise ValueError("unsupported research tool")


def _tool_child(pipe, source, action, args, records):
    try:
        result = source.execute(action, args, records)
        if len(json.dumps(result)) > 2_000_000:
            raise ValueError("tool result exceeds artifact limit")
        pipe.send((True, result))
    except Exception as exc:
        pipe.send((False, type(exc).__name__))
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
            raise RuntimeError(value)
        return value
    finally:
        parent.close()
        if process.is_alive():
            process.terminate()
        process.join(timeout=2)
        if process.is_alive():
            process.kill()
            process.join()
