"""질의 전용 불용어 (포크 #46). 색인에는 손대지 않는다.

자연어로 물으면 필러가 검색을 끌고 간다 -- `백탁 관련해서 소비자들이` 가 `['백탁','관련','소비자']` 로
쪼개지고 *"후니는 우리 소비자편이야"* 가 3위에 올라온다(ydc 실측). **IDF 로는 못 걸러낸다**: ydc
코퍼스에서 `소비자`(df 289)가 `백탁`(df 338)보다 오히려 희귀하다. 흔해서 잡음인 것이 아니라 질문을
서술하는 말이라 주제가 아니고, 그래서 통계가 아니라 판단으로 넣는 목록이다.

판단이므로 **버전을 받는 행으로 산다**(포크 #8 이 파일 사전을 막은 자리, #37 이 별칭에 낸 답과 같다):
`needs.entity_lexicon` 의 `kind='stopword'` · `canonical='query'` 활성 버전이 정본이고 레포의
`dict/query_stopwords_v1.csv` 는 그 적재 원본이다. 그 kind 는 aspect 사전과 **버전 축이 따로** 돈다 --
`entity_lexicon` 의 activate 는 `WHERE kind = %s` 라(`db/lexicon.py`) 주제 사전 개정(#56 의 v3)과 이
목록의 개정이 같은 번호를 다투지 않는다.

**활성 목록이 없는 것은 막힘이 아니다.** 주제 사전은 없으면 정답이 0건이라 점수가 거짓이 되지만
(`topics.NoDictionary`), 질의 불용어가 없는 검색은 이 목록 이전의 검색 그대로다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, LiteralString

KIND = "stopword"
# `canonical` 은 정본 표기가 아니라 이 표기가 걸리는 **축**이다. 축을 kind 로 가르면 축마다 버전 축이
# 새로 생기고, tier 로 가르면 brand 전용 어휘와 한 칸을 나눠 쓰게 된다 (formats.md §사전 CSV).
AXIS = "query"
DICTIONARY_CSV = Path(__file__).resolve().parent / "dict" / "query_stopwords_v1.csv"
FIX = f"`cosmai lexicon load --kind {KIND} --version <n> {DICTIONARY_CSV.name}` 뒤 `activate`"

ACTIVE_SQL: LiteralString = """
SELECT surface, version FROM entity_lexicon
WHERE active AND kind = %s AND canonical = %s ORDER BY id
"""
VERSION_SQL: LiteralString = """
SELECT surface, version FROM entity_lexicon
WHERE version = %s AND kind = %s AND canonical = %s ORDER BY id
"""


@dataclass(frozen=True)
class QueryStopwords:
    """질의에서 지울 토큰 한 벌. `version` 이 None 이면 활성 목록이 없다는 뜻이다."""

    words: frozenset[str]
    version: int | None = None

    def keep(self, tokens: list[str]) -> list[str]:
        raise NotImplementedError

    def dropped(self, tokens: Iterable[str]) -> list[str]:
        raise NotImplementedError


NONE = QueryStopwords(frozenset())


def from_rows(rows: Iterable[tuple[str, int]]) -> QueryStopwords:
    raise NotImplementedError


def load(conn: Any, *, version: int | None = None) -> QueryStopwords:
    raise NotImplementedError


_active: QueryStopwords | None = None
_listeners: list[Callable[[QueryStopwords], None]] = []


def on_change(listener: Callable[[QueryStopwords], None]) -> None:
    _listeners.append(listener)


def use(words: QueryStopwords) -> QueryStopwords:
    raise NotImplementedError


def forget() -> None:
    raise NotImplementedError


def active() -> QueryStopwords:
    raise NotImplementedError


def use_active(conn: Any) -> QueryStopwords:
    return use(load(conn))


def query_note(query: str) -> str | None:
    raise NotImplementedError
