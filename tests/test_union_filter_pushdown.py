"""A UNION view with a filtered output must carry that filter into every source arm (#171).

The inventory fails when another db/views UNION appears. An entry with no probe means no consumer
filters one of that UNION's direct output columns; adding such a filter requires a probe here. EXPLAIN uses
the database that tool/checks/test built from this tree, so it checks PostgreSQL's actual plan rather
than guessing expression types from the SQL text. A predicate stranded above the UNION's Append will
leave at least one source scan without the probe value.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.postgres

VIEWS = Path(__file__).resolve().parents[1] / "db" / "views"
TEXT_PROBE = "__union_probe__"
NUMBER_PROBE = "-987654321"

# view name -> (SQL filter, source relation -> minimum number of arms receiving the filter).
# The consumer filters are in portal/public/app.js, tool/sql/audit_collector_failures.sql,
# analysis/{trend,evidence,judge}/pipeline.py. The source relations are measured from the
# production EXPLAIN on 2026-09-24 and from information_schema.columns on that database.
FILTER_PROBES: dict[str, tuple[str, dict[str, int]]] = {
    "collection_lineage": (
        f"doc_key = '{TEXT_PROBE}'",
        {"review": 1, "comments": 1},
    ),
    "mention_lineage": (
        f"ref = '{TEXT_PROBE}'",
        {"need_mention": 1, "wish_mention": 1},
    ),
    "collector_health": (
        f"dataset = '{TEXT_PROBE}'",
        {"fetch_log": 1, "naver_run": 1, "jobs": 1, "collector_runs": 1},
    ),
    "metrics_topic_quarter_violation": (
        f"run_id = {NUMBER_PROBE}",
        {"metrics_topic_quarter": 2},
    ),
    "topic_quarter_evidence_violation": (
        f"run_id = {NUMBER_PROBE}",
        {"topic_quarter_evidence": 2},
    ),
    "topic_quarter_judgement_violation": (
        f"run_id = {NUMBER_PROBE}",
        {"metrics_topic_quarter": 1, "topic_quarter_judgement": 2},
    ),
}

# The author check scans all rows. Pipeline consumers fetch all stage rows or filter a computed
# freshness value after the UNION; neither filters one of the UNION's direct output columns.
UNFILTERED = {"author_identifier_violation", "pipeline_health"}


def test_every_union_view_is_accounted_for() -> None:
    union_views = set()
    for path in VIEWS.glob("*.sql"):
        sql = re.sub(r"--[^\n]*", "", path.read_text(encoding="utf-8"))
        if re.search(r"\bUNION(?:\s+ALL)?\b", sql, re.IGNORECASE):
            union_views.add(path.stem)
    assert union_views == set(FILTER_PROBES) | UNFILTERED, (
        f"unreviewed UNION views: {sorted(union_views - set(FILTER_PROBES) - UNFILTERED)}; "
        f"stale inventory: {sorted((set(FILTER_PROBES) | UNFILTERED) - union_views)}"
    )


@pytest.mark.parametrize("view", sorted(FILTER_PROBES))
def test_filtered_union_reaches_every_source_arm(harness_container: str, view: str) -> None:
    predicate, expected = FILTER_PROBES[view]
    done = subprocess.run(
        [
            "docker",
            "exec",
            harness_container,
            "psql",
            "-U",
            "fleet",
            "-d",
            "fleet",
            "-X",
            "-At",
            "-c",
            f"EXPLAIN (FORMAT JSON, COSTS OFF) SELECT * FROM needs.{view} WHERE {predicate}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, f"EXPLAIN failed for {view}: {done.stderr}"
    plan = json.loads(done.stdout)[0]["Plan"]
    counts = {relation: 0 for relation in expected}

    def visit(node: dict[str, Any]) -> None:
        relation = node.get("Relation Name")
        if isinstance(relation, str) and relation in counts:
            conditions = " ".join(
                str(node.get(key) or "") for key in ("Filter", "Index Cond", "Recheck Cond")
            )
            if TEXT_PROBE in conditions or NUMBER_PROBE in conditions:
                counts[relation] += 1
        for child in node.get("Plans", []):
            visit(child)

    visit(plan)
    missing = {
        relation: minimum - counts[relation]
        for relation, minimum in expected.items()
        if counts[relation] < minimum
    }
    assert not missing, f"{view} stranded its filter above these UNION arms: {missing}"
