from __future__ import annotations

import argparse
from urllib.parse import quote

from db import secrets
from db.seed import GROUP_NAMES, run_all
from db.seed._common import DEFAULT_SLICES

RUNTIME_KEY = "NEEDS_DB_RUNTIME"
RUNTIME_DSN = "postgresql+psycopg://needs_runtime:{password}@127.0.0.1:5434/app"


def runtime_url() -> str:
    return RUNTIME_DSN.format(password=quote(secrets.require([RUNTIME_KEY])[RUNTIME_KEY], safe=""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m db.seed", description="Load eval/ and the analysis slices into needs.*"
    )
    parser.add_argument(
        "--url", help=f"SQLAlchemy URL; default is needs_runtime with {RUNTIME_KEY} from the secret file"
    )
    parser.add_argument(
        "--slices", default=str(DEFAULT_SLICES), help="directory holding slice-*/ (default: %(default)s)"
    )
    parser.add_argument(
        "--only", default=",".join(GROUP_NAMES), help=f"comma-separated subset of {','.join(GROUP_NAMES)}"
    )
    args = parser.parse_args(argv)

    only = tuple(g.strip() for g in args.only.split(",") if g.strip())
    unknown = [g for g in only if g not in GROUP_NAMES]
    if unknown:
        parser.error(f"unknown group(s): {', '.join(unknown)}")

    for table, n in run_all(args.url or runtime_url(), slices=args.slices, only=only).items():
        print(f"{table}: {n} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
