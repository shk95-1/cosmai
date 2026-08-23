"""needs_runtime 접속 URL 한 자리. secret 은 키 이름만 남긴다 (contracts/secrets.md)."""

from __future__ import annotations

from urllib.parse import quote

from db import secrets

RUNTIME_KEY = "NEEDS_DB_RUNTIME"
RUNTIME_DSN = "postgresql+psycopg://needs_runtime:{password}@127.0.0.1:5434/app"


def runtime_url() -> str:
    return RUNTIME_DSN.format(password=quote(secrets.require([RUNTIME_KEY])[RUNTIME_KEY], safe=""))
