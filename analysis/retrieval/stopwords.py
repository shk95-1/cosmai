"""Query-only stopwords (fork #46). The index is not touched.

자연어로 물으면 필러가 검색을 끌고 간다 -- `백탁 관련해서 소비자들이` 가 `['백탁','관련','소비자']` 로
쪼개지고 *"후니는 우리 소비자편이야"* 가 3위에 올라온다(ydc 실측). **IDF 로는 못 걸러낸다**: ydc
코퍼스에서 `소비자`(df 289)가 `백탁`(df 338)보다 오히려 희귀하다. 흔해서 잡음인 것이 아니라 질문을
서술하는 말이라 주제가 아니고, 그래서 통계가 아니라 판단으로 넣는 목록이다.

판단이므로 **버전을 받는 행으로 산다**(포크 #8 이 파일 사전을 막은 자리, #37 이 별칭에 낸 답과 같다):
`needs.entity_lexicon` 의 `kind='stopword'` · `canonical='query'` 활성 버전이 정본이고 레포의
`dict/query_stopwords_v1.csv` 는 그 적재 원본이다. 그 kind 는 aspect 사전과 **활성 버전이 따로** 돈다 --
`entity_lexicon` 의 activate 는 `WHERE kind = %s` 라(`db/lexicon.py`) 주제 사전 개정(#56 의 v3)과 이
목록의 개정이 서로를 끄지 않는다. 다만 버전 **번호표**는 그 표 전역이라 이 목록을 v2 로 올리면 brand 가
그대로여도 run 의 `versions.lexicon` 이 2 가 된다 -- 물려받은 성질이고 포크 #58 이 진다
(`contracts/formats.md` §entity 사전의 `kind='stopword'`).

**A missing active list is not a blocker.** Without the topic dictionary the answer set is 0 and the score
becomes a lie (`topics.NoDictionary`), but a search without query stopwords is the search as it was before
this list existed.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, LiteralString

KIND = "stopword"
# `canonical` 은 정본 표기가 아니라 이 표기가 걸리는 **축**이다. 축을 kind 로 가르면 축마다 버전 축이
# 새로 생기고, tier 로 가르면 brand 전용 어휘와 한 칸을 나눠 쓰게 된다 (formats.md §사전 CSV).
AXIS = "query"
DICTIONARY_CSV = Path(__file__).resolve().parent / "dict" / "query_stopwords_v1.csv"
# There is no "how to fix it" string like `topics.FIX` here because there is no place to emit one -- a
# missing active list is a normal state rather than an error (`active` below), so no message telling a person
# what to run ever comes into being.

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
    """One set of tokens to drop from a query. `version` None means there is no active list."""

    words: frozenset[str]
    version: int | None = None

    def keep(self, tokens: list[str]) -> list[str]:
        """The tokens to keep. **If they are all stopwords, none is dropped** -- 0 tokens is 0 results, and
        that is worse than a ranking with fillers in it."""
        kept = [token for token in tokens if token not in self.words]
        return kept or tokens

    def dropped(self, tokens: Iterable[str]) -> list[str]:
        """The tokens taken out. It is shown to a person, so the order is kept and duplicates are folded."""
        seen = dict.fromkeys(token for token in tokens if token in self.words)
        return list(seen)


NONE = QueryStopwords(frozenset())


def from_rows(rows: Iterable[tuple[str, int]]) -> QueryStopwords:
    """(surface, version) rows into one list. With no rows it is `NONE` -- an empty list and a missing list
    mean the same to the search, so no state is made to tell them apart."""
    materialised = list(rows)
    if not materialised:
        return NONE
    return QueryStopwords(
        words=frozenset(surface for surface, _version in materialised),
        version=max(version for _surface, version in materialised),
    )


def load(conn: Any, *, version: int | None = None) -> QueryStopwords:
    """The query stopwords of the active version (or a named one). Committed after the read -- morphological
    analysis follows right after."""
    with conn.cursor() as cur:
        if version is None:
            cur.execute(ACTIVE_SQL, (KIND, AXIS))
        else:
            cur.execute(VERSION_SQL, (version, KIND, AXIS))
        rows = cur.fetchall()
    conn.commit()
    return from_rows((row[0], row[1]) for row in rows)


# Unlike `topics` there is no change notification (`on_change`) -- nothing is cached off this list, which is
# the same reason the index cache does not bite on it (the query axis builds no index).
_active: QueryStopwords | None = None


def use(words: QueryStopwords) -> QueryStopwords:
    """Makes this list the process's active list. Global for the same reason as the topic dictionary --
    there is no place to carry a connection below `bm25.tokenize_query` (the comment in `topics`)."""
    global _active
    _active = words
    return words


def forget() -> None:
    global _active
    _active = None


def active() -> QueryStopwords:
    """Not having set one and having no active version are **the same state** -- both are a search with no
    filter, and that is the search as it was before this list. So unlike the topic dictionary this does not
    stop here."""
    return _active if _active is not None else NONE


def use_active(conn: Any) -> QueryStopwords:
    return use(load(conn))


def query_note(query: str) -> str | None:
    """What was taken out of this query. None when nothing was -- the same shape as
    `pipeline.coverage_note`, and for the same reason it goes to stderr and does not change the exit code
    (it is not something to stop for)."""
    from analysis.retrieval.bm25 import tokenize

    words = active()
    tokens = tokenize(query)
    dropped = words.dropped(tokens)
    if not dropped or words.keep(tokens) == tokens:
        return None
    # Reaching here means the list is not empty, and a list that is not empty always comes from rows that
    # carry a version (`from_rows`) -- so there is no branch that says "no version".
    return f"질의 불용어(v{words.version})로 뺀 토큰: {dropped} -- 색인은 그대로다"
