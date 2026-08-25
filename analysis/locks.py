"""두 analyze 실행이 서로를 알아보는 한 자리 — 겹치면 뒤에 온 쪽이 양보한다 (#16).

`collectors/commerce/storage/locks.py` 가 소스별로 세운 것과 같은 관용구다: Postgres 세션 스코프
어드바이저리 락, `pg_try_advisory_lock` 이라 기다리지 않고, 프로세스가 죽으면 락도 같이 간다.
셋이 다르다.

  - **입도가 하나다.** 소스 락은 소스마다지만 analyze 는 전역 하나다. polarity 는 달마다 DELETE 를
    커밋한 뒤 페이지별로 다시 쓰고(analysis/polarity/pipeline.py `replace_stale`), aggregate 는
    `extractor_version` 하나만 걸고 need_mention 전량을 여러 트랜잭션에 나눠 읽는다
    (analysis/aggregate/pipeline.py `load_needs`). 한쪽이 쓰는 자리와 다른 쪽이 읽는 자리를 가르는
    분할이 없다 — scope 별로 잘라도 aggregate 는 여전히 모든 scope 를 읽고, 단계별로 잘라도
    `polarity --scope 선블록` 과 `aggregate` 는 서로 다른 단계다. 어느 쪽으로 좁혀도 리뷰가 찾은 그
    끼어들기가 그대로 남는다. 비용은 유계다: analyze 는 외부 fetch 가 없는 DB 전용 작업이고 크론은
    하루 한 줄이다 (contracts/entrypoints.md §스케줄).
  - **작업 커넥션이 락을 쥔다.** 수집기는 워커 스레드마다 커넥션을 빌리므로 락 전용 커넥션을 따로
    열어야 했고, 그래서 그 커넥션이 walk 내내 idle 로 앉아 있는 문제를 AUTOCOMMIT 으로 피해야 했다.
    analyze 는 처음부터 커넥션 하나로 도는 배치라 그 커넥션이 곧 세션이다 — 세션 스코프 락은 커밋을
    넘어 살아남으므로 배치 커밋마다 다시 잡을 필요가 없고, idle_in_transaction 도 닿지 않는다
    (락을 잡은 트랜잭션을 바로 닫는다). needs_runtime 의 CONNECTION LIMIT 도 더 먹지 않는다.
  - **기다리지 않는 이유가 하나 더 있다.** 사람이 손으로 도는 gemma4 패스는 2.5~4시간이다. 그 뒤에
    줄 선 05:00 은 다음 05:00 이 와도 줄에 있고, 하필 사람이 다음 패스를 시작하려는 순간에 락을
    받는다. 못 잡으면 건너뛰고 사유를 남기고 partial(종료 코드 1)로 끝난다 — 모든 단계가 자연키
    upsert 라 건너뛴 밤은 다음 실행이 그대로 가져간다 (contracts/entrypoints.md §분석).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, LiteralString

import psycopg

__all__ = ["ANALYZE", "LOCK_CLASS", "advisory_key", "analyze_lock"]

# 락을 들인 이슈 번호를 네임스페이스로 쓴다 — 수집기의 10(#10)과 같은 규약이고, classid 가 다르므로
# 두 락은 무슨 수를 써도 부딪히지 않는다. pricing.py 의 한 인자 형태(6)와도 공간이 다르다.
LOCK_CLASS = 16
ANALYZE = "analyze"

TAKE: LiteralString = "SELECT pg_try_advisory_lock(%s, %s)"
GIVE_BACK: LiteralString = "SELECT pg_advisory_unlock(%s, %s)"


def advisory_key(name: str) -> tuple[int, int]:
    """이 락이 사는 (classid, objid). blake2b 인 이유는 `hash()` 가 프로세스마다 소금을 치기 때문이다 —
    조정해야 하는 상대가 다른 프로세스라 05:00 과 08:00 이 다른 숫자를 잠그면 아무것도 조정하지 못한다.
    objid 가 int4 라 4바이트."""
    digest = hashlib.blake2b(name.encode("utf-8"), digest_size=4).digest()
    return LOCK_CLASS, int.from_bytes(digest, "big", signed=True)


@contextmanager
def analyze_lock(conn: psycopg.Connection[Any], name: str = ANALYZE) -> Iterator[bool]:
    """잡았으면 True 를 내주고 블록이 끝날 때 돌려준다. 못 잡았으면 False — 호출자가 양보한다."""
    classid, objid = advisory_key(name)
    with conn.cursor() as cur:
        cur.execute(TAKE, (classid, objid))
        row = cur.fetchone()
    held = bool(row and row[0])
    # 락은 세션의 것이라 이 커밋을 넘어 남는다 — 열어 둔 채 두면 idle_in_transaction 15s 가 세션을 끊는다.
    conn.commit()
    try:
        yield held
    finally:
        if held:
            try:
                conn.rollback()  # 단계가 실패한 트랜잭션을 남겼으면 여기서 아무 문장도 나가지 못한다.
                with conn.cursor() as cur:
                    cur.execute(GIVE_BACK, (classid, objid))
                    row = cur.fetchone()
                conn.commit()
                # 수집기와 같은 한 줄이다: 2.5~4시간짜리 실행에서 이것이 "둘이 겹쳤을 수 있다"의
                # 유일한 사후 증거다 — 반환값을 버리면 그 사실을 아무도 모른다.
                if not (row and row[0]):
                    print(
                        f"{name} lock: pg_advisory_unlock says this session did not hold it, so the "
                        "lock went sometime during the run and the run was not told"
                    )
            # 세션이 이미 갔으면 락도 같이 갔다 — 크론 메일에 트레이스백 대신 한 줄.
            except psycopg.Error as unreachable:
                print(f"analyze lock: not given back -- {str(unreachable).splitlines()[0]}")
