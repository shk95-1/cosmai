"""Reads ~/.config/cosmai/env. Key names may be logged; values never are."""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

ENV_PATH_VAR = "COSMAI_SECRET_FILE"
DEFAULT_PATH = Path.home() / ".config" / "cosmai" / "env"


def path_for(path: str | Path | None = None) -> Path:
    return Path(path or os.environ.get(ENV_PATH_VAR) or DEFAULT_PATH).expanduser()


def load(path: str | Path | None = None) -> dict[str, str]:
    secret_file = path_for(path)
    if not secret_file.is_file():
        return {}
    out: dict[str, str] = {}
    for line in secret_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        out[key.strip()] = value.strip().strip("'\"")
    return out


def require(keys: Iterable[str], path: str | Path | None = None) -> dict[str, str]:
    """Exits naming only the missing keys -- a value must never reach a log or a traceback."""
    secrets = load(path)
    wanted = list(keys)
    missing = [k for k in wanted if not secrets.get(k)]
    if missing:
        raise SystemExit(f"missing in {path_for(path)}: {', '.join(missing)}")
    return {k: secrets[k] for k in wanted}
