"""`cosmai eval <task>`: needs.labeled_set 의 골드와 등록된 구현체의 예측을 맞춰 점수를 낸다."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, LiteralString

import psycopg

from analysis.baselines import EvalSet, for_task
from analysis.metrics import Scores, collapsed_accuracy, score
from analysis.registry import Implementation, LabeledRow

ROWS: LiteralString = """
SELECT ref, split, gold, coalesce(text, ''), coalesce(extra, '{}'::jsonb)
FROM labeled_set WHERE task = %s AND split = %s ORDER BY ref
"""
RUN: LiteralString = """
INSERT INTO analysis_run (finished_at, status, versions, note)
VALUES (now(), 'ok', %s::jsonb, %s) RETURNING run_id
"""

STRICT = frozenset({"Y"})
# 'V' = 변형, 'Y?' = 사람이 확신하지 못한 같음. 변형허용은 둘 다 같은 제품으로 친다.
LENIENT = frozenset({"Y", "V", "Y?"})


@dataclass(frozen=True)
class SetResult:
    name: str
    scores: Scores
    metrics: Mapping[str, float]
    misses: tuple[str, ...]


def _normalize(task: str, label: str) -> str:
    # brand_link 의 라벨은 'OK(retailer)' 처럼 이유를 달고 온다 — 정밀도는 OK/FP 두 갈래로만 센다.
    if task == "brand_link":
        return "FP" if label.upper().startswith("FP") else "OK"
    return label


def _rows(conn: psycopg.Connection[Any], task: str, eval_set: EvalSet) -> tuple[LabeledRow, ...]:
    with conn.cursor() as cur:
        cur.execute(ROWS, (task, eval_set.split))
        found = cur.fetchall()
    roster = eval_set.refs()
    rows = tuple(
        LabeledRow(task=task, ref=ref, split=split, gold=gold, text=text, extra=extra)
        for ref, split, gold, text, extra in found
        if (roster is None or ref in roster) and ref.startswith(eval_set.ref_prefix)
    )
    if not rows:
        raise LookupError(f"labeled_set has no rows for {task} / {eval_set.name}")
    return rows


def _metrics(task: str, pairs: Sequence[tuple[str, str]], scores: Scores) -> dict[str, float]:
    out = {"acc": scores.accuracy}
    for klass in scores.classes:
        out[f"P:{klass.label}"] = klass.precision
        out[f"R:{klass.label}"] = klass.recall
    if task == "product_match":
        out["strict"] = collapsed_accuracy(pairs, STRICT)
        out["변형허용"] = collapsed_accuracy(pairs, LENIENT)
    return out


def evaluate(conn: psycopg.Connection[Any], task: str, impl: Implementation) -> tuple[SetResult, ...]:
    results = []
    for eval_set in for_task(task):
        rows = _rows(conn, task, eval_set)
        predictions = impl.predict(rows)
        if len(predictions) != len(rows):
            raise LookupError(
                f"{impl.version} returned {len(predictions)} prediction(s) "
                f"for {len(rows)} row(s) in {eval_set.name!r}"
            )
        pairs = [
            (_normalize(task, row.gold), _normalize(task, str(pred)))
            for row, pred in zip(rows, predictions, strict=True)
        ]
        scores = score(pairs)
        metrics = _metrics(task, pairs, scores)
        misses = tuple(
            f"{eval_set.name}: {check.metric} {metrics.get(check.metric, 0.0):.3f} < {check.threshold:.2f}"
            for check in eval_set.checks
            if metrics.get(check.metric, 0.0) < check.threshold
        )
        results.append(SetResult(name=eval_set.name, scores=scores, metrics=metrics, misses=misses))
    return tuple(results)


def render(task: str, version: str, results: Sequence[SetResult]) -> str:
    lines = [f"{task} · {version}"]
    for result in results:
        extra = "  ".join(
            f"{name} {result.metrics[name]:.3f}" for name in ("strict", "변형허용") if name in result.metrics
        )
        head = f"  {result.name}  n={result.scores.n}  acc {result.scores.accuracy:.3f}"
        lines.append(f"{head}  {extra}" if extra else head)
        lines.append(f"    {'label':<10}{'gold':>6}{'pred':>6}{'P':>8}{'R':>8}")
        for klass in result.scores.classes:
            lines.append(
                f"    {klass.label:<10}{klass.support:>6}{klass.predicted:>6}"
                f"{klass.precision:>8.3f}{klass.recall:>8.3f}"
            )
    return "\n".join(lines)


def record(conn: psycopg.Connection[Any], task: str, version: str, results: Sequence[SetResult]) -> int:
    # note 는 계약이 정한 문자열 그대로라(entrypoints 판정) 점수는 같은 행의 jsonb 에 남긴다.
    versions = {task: version, "scores": {r.name: dict(r.metrics) for r in results}}
    with conn.cursor() as cur:
        cur.execute(RUN, (json.dumps(versions, ensure_ascii=False), f"eval:{task}:{version}"))
        row = cur.fetchone()
    conn.commit()
    return int(row[0]) if row else 0
