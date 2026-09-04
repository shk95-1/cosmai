"""`python -m db.corpus load <dir>` · `python -m db.corpus verify` (포크 #4).

`db/seed` 와 같은 모양의 CLI 지만 그룹이 아니라 하위 명령이 둘이다: 적재는 레포 밖 경로를 인자로
받고(원본은 `archive/` 에 있고 그 자리는 수정 금지다), 검증은 적재된 행 위에서 매니페스트의
`reproduces` 를 다시 센다.
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
    # --url 은 하위 명령 **뒤**에도 오도록 부모 파서로 둔다: `load <dir> --url ...` 가 손이 가는
    # 순서인데, 최상위에만 있으면 그 줄이 unrecognized arguments 로 죽는다.
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
