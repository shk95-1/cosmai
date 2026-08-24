"""needs_runtime 접속 URL 한 자리. secret 은 키 이름만 남긴다 (contracts/secrets.md)."""

from __future__ import annotations

import os
from urllib.parse import quote

from db import secrets

RUNTIME_KEY = "NEEDS_DB_RUNTIME"
# 호스트에서는 shared-postgres 의 게시 포트가, 컴포즈 망 안에서는 서비스명:5432 가 같은 DB 다.
# 롤·DB 이름·secret 키 이름은 계약이라 움직이지 않는다 (contracts/secrets.md).
HOST_VAR = "COSMAI_DB_HOST"
PORT_VAR = "COSMAI_DB_PORT"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = "5434"
RUNTIME_DSN = "postgresql+psycopg://needs_runtime:{password}@{host}:{port}/app"


def host_and_port(host: str | None = None, port: int | str | None = None) -> tuple[str, str]:
    """이 저장소에서 DSN 을 짓는 세 자리(여기·commerce·youtube)가 같은 노브를 같은 규칙으로 읽게 하는 한 자리.

    명시 인자가 env 를 이긴다: 인자를 준 호출자는 특정 DB 를 겨냥한 것이고, env 는 아무도 지목하지
    않았을 때의 배포 단위 기본값이다.
    """
    # compose 는 값이 없는 ${VAR} 를 빈 문자열로 넘긴다 — 빈 호스트가 아니라 기본값이어야 한다.
    return (
        str(host or os.environ.get(HOST_VAR) or DEFAULT_HOST),
        str(port or os.environ.get(PORT_VAR) or DEFAULT_PORT),
    )


def runtime_url() -> str:
    password = quote(secrets.require([RUNTIME_KEY])[RUNTIME_KEY], safe="")
    host, port = host_and_port()
    return RUNTIME_DSN.format(password=password, host=host, port=port)
