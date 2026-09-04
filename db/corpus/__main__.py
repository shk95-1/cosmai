"""`python -m db.corpus load <dir>` -- `python -m db.corpus verify` (fork #4).

A CLI shaped the same way as `db/seed`, but with two subcommands rather than groups: load takes a path
outside the repo as an argument (the source lives in `archive/`, and that spot may not be modified),
while verify recounts the manifest's `reproduces` on top of the rows already loaded.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from db import corpus
from db.corpus import verify
from db.runtime import RUNTIME_KEY, runtime_url
from db.seed._common import connect


def _progress(label: str, seen: int) -> None:
    print(f"{label}: {seen} rows", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m db.corpus", description="Import and verify the ydc youtube corpus snapshot"
    )
    # --url is put on the parent parser so it can also come **after** the subcommand: `load <dir>
    # --url ...` is the order a hand naturally reaches for, and if it only lived at the top level that
    # line would die with unrecognized arguments.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--url", help=f"SQLAlchemy URL; default is needs_runtime with {RUNTIME_KEY} from the secret file"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    load = sub.add_parser(
        "load", parents=[common], help="import document/mention/channel CSVs from a directory"
    )
    load.add_argument(
        "source_dir", type=Path, help="directory holding document.csv, mention.csv, manifest.json"
    )
    load.add_argument("--snapshot-id", type=int, default=corpus.SNAPSHOT_ID)
    load.add_argument("--label", default=corpus.SNAPSHOT_LABEL)
    load.add_argument("--batch", type=int, default=corpus.BATCH)
    load.add_argument(
        "--no-activate",
        action="store_true",
        help="load the rows without pointing the active snapshot at them",
    )
    load.add_argument("--quiet", action="store_true", help="no per-page progress on stderr")

    check = sub.add_parser("verify", parents=[common], help="recount manifest.reproduces on the loaded rows")
    check.add_argument("--snapshot-id", type=int, help="default: the active snapshot")
    check.add_argument(
        "--expect",
        type=Path,
        help="manifest.json to compare against; exit 1 when a number differs",
    )

    args = parser.parse_args(argv)
    url = args.url or runtime_url()

    if args.command == "load":
        with connect(url) as conn:
            for table, n in corpus.load(
                conn,
                args.source_dir,
                snapshot_id=args.snapshot_id,
                label=args.label,
                activate_snapshot=not args.no_activate,
                batch=args.batch,
                progress=None if args.quiet else _progress,
            ).items():
                print(f"{table}: {n} rows")
        return 0

    with connect(url) as conn:
        measured = verify.reproduce(conn, snapshot_id=args.snapshot_id)
    for key, value in measured.items():
        print(f"{key}: {value}")
    if not args.expect:
        return 0
    expected = json.loads(args.expect.read_text(encoding="utf-8"))["reproduces"]
    off = {k: (v, expected.get(k)) for k, v in measured.items() if expected.get(k) != v}
    if off:
        for key, (got, want) in off.items():
            print(f"MISMATCH {key}: measured {got}, manifest {want}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
