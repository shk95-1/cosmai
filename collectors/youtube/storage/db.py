"""The Postgres connection -- production URL only. #8 has no live transport (issue #8: "라이브 yt-dlp
호출 없음"), so unlike collectors/commerce there is no engine/journal here: tests inject a fake fetcher
straight into `cli.run` and write through the plain SQLAlchemy connection they already hold.

origin: service/yt-scrapper/src/tubedepth/database.py -- ported for #8, pointed at this repo's
`db.secrets` the way collectors/commerce/storage/db.py points trend_radar at it -- its own
TUBEDEPTH_DB_RUNTIME secret, not the repo's general COSMA_DB_RUNTIME: the old stack still runs
tubedepth_runtime with its own password from its own `.env`, distinct from both COSMA_DB_RUNTIME
and trend_radar_runtime's password, so the shared key connected with the wrong password (#29).
tubedepth's own runtime role -- epic #16 판정 1's "tubedepth pattern".
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import Engine
from sqlalchemy.engine import make_url

from collectors.youtube.storage.schema import SERVICE_SCHEMA
from db import secrets
from db.runtime import host_and_port

# #29: no fallback to COSMA_DB_RUNTIME -- a missing key must fail by this name, not silently
# connect as tubedepth_runtime with some other role's password.
RUNTIME_SECRET_KEY = "TUBEDEPTH_DB_RUNTIME"


def runtime_url(host: str | None = None, port: int | str | None = None, database: str = "app") -> str:
    """Host and port come from `db.runtime`'s COSMAI_DB_HOST/COSMAI_DB_PORT so a container reaches the
    same database the host does; the role, the database and the secret key stay tubedepth's own
    (contracts/entrypoints.md §DB 접속 노브)."""
    password = secrets.require([RUNTIME_SECRET_KEY])[RUNTIME_SECRET_KEY]
    host, port = host_and_port(host, port)
    url = make_url(f"postgresql+psycopg://{SERVICE_SCHEMA}_runtime:{password}@{host}:{port}/{database}")
    return url.update_query_dict({"options": f"-csearch_path={SERVICE_SCHEMA},pg_catalog"}).render_as_string(
        hide_password=False
    )


def create_engine(url: str) -> Engine:
    return sa.create_engine(url, pool_pre_ping=True, connect_args={"application_name": "cosmai-youtube"})


__all__ = ["runtime_url", "create_engine"]
