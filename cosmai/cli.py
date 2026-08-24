"""`cosmai` -- the one console entrypoint (contracts/entrypoints.md). `python -m cosmai.cli` so
`--help` is checkable offline in a subprocess without an installed console script (playbook
snippets/test_stack_commands_resolve.py).

`collect commerce`/`youtube` are wired (#7, #8); `naver` is #9 and refuses cleanly until then --
declared here anyway so this module is the one place stack/crontab and stack/docker-compose.yml can be
checked against, per contracts/entrypoints.md's collector list.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from analysis.registry import TASKS

if TYPE_CHECKING:  # psycopg 를 여기서 import 하면 --help 한 번에도 드라이버가 딸려 온다.
    import psycopg

STAGES = ("link", "polarity", "aggregate", "all")
SPLITS = ("tune", "holdout")
KINDS = ("brand", "format", "attribute", "ingredient", "stopword", "alias", "aspect")


def _add_collect(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("collect", help="Run one collector's dataset for one hour.")
    p.add_argument("collector", choices=["commerce", "youtube", "naver"])
    p.add_argument("--dataset", required=True, help="Which dataset to collect.")
    p.add_argument("--board", default=None, help="commerce review_low only: which board to walk.")
    p.add_argument("--since", default=None, help="Accepted for shape; unused by every dataset today.")


def _add_analyze(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("analyze", help="Run one analysis stage over the needs schema.")
    p.add_argument("stage", choices=STAGES)
    p.add_argument("--since", default=None, help="Only units observed on or after this date.")
    p.add_argument("--scope", default=None, help="Restrict to one lexicon category.")


def _add_eval(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("eval", help="Score one task against needs.labeled_set.")
    p.add_argument("task", choices=TASKS)
    p.add_argument("--check-baseline", action="store_true", help="Exit 1 when a baseline is missed.")
    p.add_argument("--impl", default=None, help="Registered factory to use, e.g. llm:claude-sonnet-5.")
    p.add_argument("--split", default=None, choices=SPLITS, help="Only score eval sets of this split.")
    p.add_argument(
        "--url", default=None, help="SQLAlchemy URL; default is needs_runtime from the secret file."
    )


def _add_lexicon(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("lexicon", help="Load, compare and activate a dictionary version.")
    actions = p.add_subparsers(dest="action", required=True)
    for name, helptext in (
        ("load", "Load one CSV as version n; a version already loaded is a no-op."),
        ("diff", "Show what version n adds, drops and rewrites against another version."),
        ("activate", "Make version n the active one and deactivate the rest of that kind."),
    ):
        sub = actions.add_parser(name, help=helptext)
        sub.add_argument("--kind", required=True, choices=KINDS, help="Which dictionary.")
        sub.add_argument("--version", required=True, type=int, help="The dictionary version to act on.")
        sub.add_argument("--url", default=None, help="SQLAlchemy URL; default is needs_runtime.")
        if name == "load":
            sub.add_argument("csv", help="CSV in the shape contracts/formats.md gives for this kind.")
        if name == "diff":
            sub.add_argument(
                "--against", type=int, default=None, help="Version to compare with (default: active)."
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cosmai")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_collect(subparsers)
    _add_analyze(subparsers)
    _add_eval(subparsers)
    _add_lexicon(subparsers)
    return parser


def _run_collect(args: argparse.Namespace) -> int:
    if args.collector == "commerce":
        from collectors.commerce.cli import run

        return run(args.dataset, board=args.board, since=args.since)
    if args.collector == "youtube":
        from collectors.youtube.cli import run

        return run(args.dataset, board=args.board, since=args.since)
    if args.collector == "naver":
        from collectors.naver.cli import run

        return run(args.dataset, board=args.board, since=args.since)
    raise AssertionError(f"unreachable: argparse choices are exhausted, got {args.collector!r}")


def _run_analyze(args: argparse.Namespace) -> int:
    print(f"analyze {args.stage} is not wired yet (issues #2/#3/#4/#5)")
    return 2


def _connect(url: str | None) -> psycopg.Connection[Any]:
    from db.runtime import runtime_url
    from db.seed._common import connect

    return connect(url or runtime_url())


def _run_eval(args: argparse.Namespace) -> int:
    from analysis import registry
    from analysis.baselines import adoption_misses
    from analysis.evaluate import evaluate, record, render

    registry.load_implementations()
    try:
        impl = registry.build(args.task, args.impl) if args.impl else registry.get(args.task)
    except LookupError as refused:
        print(refused)
        return 2
    # 기준선 표는 홀드아웃 셋을 먼저 돌려준다 (analysis/baselines.py) — 유료 구현을 --split 없이 부르면
    # 블라인드 홀드아웃이 첫 호출로 나간다. 규율이 아니라 인자로 막는다.
    if args.impl and registry.is_paid(args.task, args.impl) and args.split is None:
        print(f"--impl {args.impl} spends money; pass --split tune or --split holdout")
        return 2
    if impl is None:
        print(
            f"no implementation registered for {args.task!r}; the unit that owns it calls "
            "analysis.registry.register() at import time"
        )
        return 2
    import psycopg

    with _connect(args.url) as conn:
        try:
            results = evaluate(conn, args.task, impl, split=args.split)
            print(render(args.task, impl.version, results))
            print(f"analysis_run {record(conn, args.task, impl.version, results)}")
        except (LookupError, psycopg.Error) as unusable:
            print(unusable)
            return 2
    if args.check_baseline:
        misses = [miss for result in results for miss in result.misses]
        try:
            # 채택 조건은 바닥이고, 교체 조건은 규칙 실측 이상이다 (interfaces.md §규칙 실측).
            misses += list(adoption_misses(args.task, {r.name: dict(r.metrics) for r in results}))
        except LookupError as unusable:
            print(unusable)
            return 2
        for miss in misses:
            print(miss)
        return 1 if misses else 0
    return 0


def _csv_rows(kind: str, path: str) -> list[tuple[object, ...]]:
    """formats.md 의 사전 CSV → db.lexicon 의 컬럼 순서. kind 열이 있으면 --kind 와 같아야 한다."""
    from db.lexicon import ASPECT_COLUMNS, ASPECT_KIND, ENTITY_COLUMNS
    from db.seed._common import boolean, opt, read_csv

    rows = read_csv(Path(path))
    if not rows:
        raise ValueError(f"{path} has no rows")
    wanted = ASPECT_COLUMNS if kind == ASPECT_KIND else ENTITY_COLUMNS[1:]
    missing = [c for c in wanted if c not in rows[0]]
    if missing:
        raise ValueError(f"{path} is missing column(s): {', '.join(missing)}")
    mislabelled = {r["kind"] for r in rows if "kind" in r} - {kind}
    if mislabelled:
        raise ValueError(f"{path} carries kind(s) {', '.join(sorted(mislabelled))}, not {kind}")
    if kind == ASPECT_KIND:
        return [
            (
                r["aspect"],
                r["scope"],
                r["category"],
                r["pattern"],
                boolean(r["is_neutral_noun"]),
                r["ruleset"],
                int(r["priority"]),
            )
            for r in rows
        ]
    return [
        (kind, r["canonical"], r["surface"], opt(r["tier"]), opt(r["source"]), opt(r["note"])) for r in rows
    ]


def _run_lexicon(args: argparse.Namespace) -> int:
    import psycopg

    from db.lexicon import ASPECT_KIND, activate, active_version, diff, insert_aspects, insert_entities

    with _connect(args.url) as conn, conn.cursor() as cur:
        try:
            if args.action == "load":
                rows = _csv_rows(args.kind, args.csv)
                insert = insert_aspects if args.kind == ASPECT_KIND else insert_entities
                # 새 버전은 꺼진 채로 들어온다 — 교체는 activate 가 한다 (formats.md).
                loaded = insert(cur, rows, args.version, active=False)
                conn.commit()
                print(
                    f"{args.kind} v{args.version}: {len(rows)} in the csv, {loaded} loaded, "
                    f"{len(rows) - loaded} already there"
                )
                return 0
            if args.action == "activate":
                touched = activate(cur, args.kind, args.version)
                conn.commit()
                print(f"{args.kind} v{args.version} is now the active version ({touched} rows touched)")
                return 0
            against = args.against if args.against is not None else active_version(cur, args.kind)
            if against is None:
                print(f"{args.kind} has no active version to compare with; pass --against")
                return 2
            d = diff(cur, args.kind, args.version, against)
        # 잘못된 CSV 도 CHECK 위반도 blocked 다 — 트레이스백은 종료 코드 1 이 되어 규약을 깬다.
        except (ValueError, LookupError, psycopg.Error) as refused:
            print(refused)
            return 2
    print(f"{d.kind} v{d.version} vs v{d.against}: +{len(d.added)} -{len(d.removed)} ~{len(d.changed)}")
    for mark, keys in (("+", d.added), ("-", d.removed), ("~", d.changed)):
        for key in keys:
            print(f"{mark} {key}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "collect":
        return _run_collect(args)
    if args.command == "analyze":
        return _run_analyze(args)
    if args.command == "eval":
        return _run_eval(args)
    if args.command == "lexicon":
        return _run_lexicon(args)
    parser.error(f"unknown command {args.command!r}")  # pragma: no cover - argparse already refused this
    return 2


if __name__ == "__main__":
    sys.exit(main())
