"""Offline replay and a strictly read-only export; never a paid model invocation."""

from __future__ import annotations

import json
from pathlib import Path

from analysis.discovery.core import select


def run(args) -> int:
    if args.action == "sample":
        from analysis.discovery.sample import sample_local

        result = sample_local(args.per_cue)
    else:
        result = select(json.loads(Path(args.sample).read_text()), json.loads(Path(args.review).read_text()))
    output = Path(args.output)
    # Research artifacts contain stored author hashes and full excerpts. Private by default;
    # the public report/review fixtures can be separately redacted and intentionally committed.
    with output.open("w", encoding="utf-8") as f:
        output.chmod(0o600)
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(
        json.dumps(
            {
                "action": args.action,
                "documents": len(result.get("documents", [])),
                "status": result.get("status", "sampled"),
                "selected": result.get("selected"),
                "failures": result["manifest"].get("failures", []),
            },
            sort_keys=True,
        )
    )
    return 0
