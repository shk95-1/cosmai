"""The Postgres connection -- production URL only. #8 has no live transport (issue #8: "라이브 yt-dlp
호출 없음"), so unlike collectors/commerce there is no engine/journal here: tests inject a fake fetcher
straight into `cli.run` and write through the plain SQLAlchemy connection they already hold.

origin: service/yt-scrapper/src/tubedepth/database.py -- ported for #8, pointed at this repo's
`db.secrets` the way collectors/commerce/storage/db.py points trend_radar at it (same
COSMA_DB_RUNTIME secret, tubedepth's own runtime role -- epic #16 판정 1's "tubedepth pattern").
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import Engine
from sqlalchemy.engine import make_url

from collectors.youtube.storage.schema import SERVICE_SCHEMA
from db import secrets


def runtime_url(host: str = "127.0.0.1", port: int = 5434, database: str = "app") -> str:
    password = secrets.require(["COSMA_DB_RUNTIME"])["COSMA_DB_RUNTIME"]
    url = make_url(f"postgresql+psycopg://{SERVICE_SCHEMA}_runtime:{password}@{host}:{port}/{database}")
    return url.update_query_dict({"options": f"-csearch_path={SERVICE_SCHEMA},pg_catalog"}).render_as_string(
        hide_password=False
    )


def create_engine(url: str) -> Engine:
    return sa.create_engine(url, pool_pre_ping=True, connect_args={"application_name": "cosmai-youtube"})


__all__ = ["runtime_url", "create_engine"]
