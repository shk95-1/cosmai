"""`cosmai` -- the one console entrypoint (contracts/entrypoints.md). `python -m cosmai.cli` so
`--help` is checkable offline in a subprocess without an installed console script (playbook
snippets/test_stack_commands_resolve.py).

Only `collect commerce` is wired for #7; `youtube`/`naver` are #8/#9 and refuse cleanly until then --
declared here anyway so this module is the one place stack/crontab and stack/docker-compose.yml can be
checked against, per contracts/entrypoints.md's collector list.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence


def _add_collect(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("collect", help="Run one collector's dataset for one hour.")
    p.add_argument("collector", choices=["commerce", "youtube", "naver"])
    p.add_argument("--dataset", required=True, help="Which dataset to collect.")
    p.add_argument("--board", default=None, help="commerce review_low only: which board to walk.")
    p.add_argument("--since", default=None, help="Accepted for shape; unused by every dataset today.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cosmai")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_collect(subparsers)
    return parser


def _run_collect(args: argparse.Namespace) -> int:
    if args.collector != "commerce":
        print(f"collector {args.collector!r} is not wired yet (see issue #8/#9)")
        return 2
    from collectors.commerce.cli import run

    return run(args.dataset, board=args.board, since=args.since)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "collect":
        return _run_collect(args)
    parser.error(f"unknown command {args.command!r}")  # pragma: no cover - argparse already refused this
    return 2


if __name__ == "__main__":
    sys.exit(main())
