"""`cosmai eval <task>`: matches the gold of needs.labeled_set with the predictions of a registered
implementation and scores them."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, LiteralString

import psycopg

from analysis.baselines import EvalSet, for_task, meets
from analysis.metrics import Scores, precision_over, score
from analysis.registry import Implementation
from analysis.types import LabeledRow

ROWS: LiteralString = """
SELECT ref, split, gold, coalesce(text, ''), coalesce(extra, '{}'::jsonb)
FROM labeled_set WHERE task = %s AND split = %s ORDER BY ref
"""
RUN: LiteralString = """
INSERT INTO analysis_run (finished_at, status, versions, note)
VALUES (now(), 'ok', %s::jsonb, %s) RETURNING run_id
"""

# 채택/비채택은 예측 라벨이고, 분모는 채택한 쌍이다 (interfaces.md §기준선).
ADOPTED = frozenset({"Y"})
REJECTED = frozenset({"N"})
STRICT_GOLD = frozenset({"Y"})
LENIENT_GOLD = frozenset({"Y", "V"})
PRODUCT_MATCH_GOLD = frozenset({"Y", "V", "N"})
# A brand_link label that arrives with a reason attached, such as 'OK(retailer)'. Only the head is accepted
# by whitelist and the rest is refused -- folding an unknown label quietly to OK inflates the precision with
# nobody the wiser.
BRAND_LINK_GOLD = re.compile(r"^(OK|FP)\b")


@dataclass(frozen=True)
class SetResult:
    name: str
    scores: Scores
    metrics: Mapping[str, float]
    misses: tuple[str, ...]


def _normalize(task: str, label: str, where: str) -> str:
    if task == "brand_link":
        folded = BRAND_LINK_GOLD.match(label)
        if not folded:
            raise LookupError(f"{where}: brand_link label {label!r} is neither OK... nor FP...")
        return folded.group(1)
    return label


def _refuse_unknown_match_labels(pairs: Sequence[tuple[str, str]], where: str) -> None:
    """gold is Y|V|N and a prediction is accepted Y | not accepted N. Any other label changes the denominator
    quietly, so it is refused."""
    bad_gold = {gold for gold, _ in pairs} - PRODUCT_MATCH_GOLD
    if bad_gold:
        raise LookupError(f"{where}: gold {', '.join(sorted(bad_gold))} is not one of Y, V, N")
    bad_pred = {pred for _, pred in pairs} - ADOPTED - REJECTED
    if bad_pred:
        raise LookupError(
            f"{where}: prediction {', '.join(sorted(bad_pred))} is neither Y (accept) nor N (reject)"
        )


def _rows(conn: psycopg.Connection[Any], task: str, eval_set: EvalSet) -> tuple[LabeledRow, ...]:
    with conn.cursor() as cur:
        cur.execute(ROWS, (task, eval_set.split))
        found = cur.fetchall()
    rows = tuple(
        LabeledRow(task=task, ref=ref, split=split, gold=gold, text=text, extra=extra)
        for ref, split, gold, text, extra in found
        if ref.startswith(eval_set.ref_prefix)
        and (not eval_set.extra_key or extra.get(eval_set.extra_key) == eval_set.extra_value)
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
        out["strict"] = precision_over(pairs, ADOPTED, STRICT_GOLD)
        out["변형허용"] = precision_over(pairs, ADOPTED, LENIENT_GOLD)
    return out


def evaluate(
    conn: psycopg.Connection[Any], task: str, impl: Implementation, split: str | None = None
) -> tuple[SetResult, ...]:
    # Filtering by split is not about hiding a score: not running the holdout at all is what keeps the blind
    # blind during tuning, and on a paid implementation (#6) that much money is not spent.
    sets = [s for s in for_task(task) if split is None or s.split == split]
    loaded = [(eval_set, _rows(conn, task, eval_set)) for eval_set in sets]
    # The read transaction is closed before predicting: idle_in_transaction_session_timeout 15s
    # (db/bootstrap.sql) cuts the session of an implementation that runs with it open (the LLM batch of #6).
    conn.rollback()
    results = []
    for eval_set, rows in loaded:
        predictions = impl.predict(rows)
        if len(predictions) != len(rows):
            raise LookupError(
                f"{impl.version} returned {len(predictions)} prediction(s) "
                f"for {len(rows)} row(s) in {eval_set.name!r}"
            )
        pairs = [
            (
                _normalize(task, row.gold, f"{eval_set.name} gold {row.ref}"),
                _normalize(task, str(pred), f"{eval_set.name} prediction {row.ref}"),
            )
            for row, pred in zip(rows, predictions, strict=True)
        ]
        if task == "product_match":
            _refuse_unknown_match_labels(pairs, eval_set.name)
        scores = score(pairs)
        metrics = _metrics(task, pairs, scores)
        misses = tuple(
            f"{eval_set.name}: {check.metric} {metrics.get(check.metric, 0.0):.3f} < {check.threshold}"
            for check in eval_set.checks
            if not meets(metrics.get(check.metric, 0.0), check.threshold)
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
    # note is exactly the string the contract fixed, so the scores go into the jsonb of the same row.
    versions = {task: version, "scores": {r.name: dict(r.metrics) for r in results}}
    with conn.cursor() as cur:
        cur.execute(RUN, (json.dumps(versions, ensure_ascii=False), f"eval:{task}:{version}"))
        row = cur.fetchone()
    conn.commit()
    return int(row[0]) if row else 0
