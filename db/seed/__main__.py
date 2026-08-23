from __future__ import annotations

import argparse

from db.runtime import RUNTIME_KEY, runtime_url
from db.seed import GROUP_NAMES, run_all
from db.seed._common import DEFAULT_SLICES


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
        "--only",
        default=",".join(GROUP_NAMES),
        help=f"comma-separated subset of {','.join(GROUP_NAMES)}; run in that order, "
        "since mentions reference product_ref and metrics reference analysis_run",
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
