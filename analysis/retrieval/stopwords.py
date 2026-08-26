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

from collections.abc import Iterable
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
        """남길 토큰. **전부 불용어면 하나도 지우지 않는다** -- 토큰 0개는 결과 0건이고, 그것은
        필러가 낀 순위보다 나쁘다."""
        kept = [token for token in tokens if token not in self.words]
        return kept or tokens

    def dropped(self, tokens: Iterable[str]) -> list[str]:
        """뺀 토큰. 사람에게 보여주는 용도라 순서를 지키고 중복을 접는다."""
        seen = dict.fromkeys(token for token in tokens if token in self.words)
        return list(seen)


NONE = QueryStopwords(frozenset())


def from_rows(rows: Iterable[tuple[str, int]]) -> QueryStopwords:
    """(surface, version) 행들을 목록 하나로. 행이 없으면 `NONE` 이다 -- 빈 목록과 없는 목록은
    검색에 같은 뜻이라 둘을 가르는 상태를 만들지 않는다."""
    materialised = list(rows)
    if not materialised:
        return NONE
    return QueryStopwords(
        words=frozenset(surface for surface, _version in materialised),
        version=max(version for _surface, version in materialised),
    )


def load(conn: Any, *, version: int | None = None) -> QueryStopwords:
    """활성 버전(또는 지정한 버전)의 질의 불용어. 읽고 나서 커밋한다 -- 뒤이어 형태소 분석이 붙는다."""
    with conn.cursor() as cur:
        if version is None:
            cur.execute(ACTIVE_SQL, (KIND, AXIS))
        else:
            cur.execute(VERSION_SQL, (version, KIND, AXIS))
        rows = cur.fetchall()
    conn.commit()
    return from_rows((row[0], row[1]) for row in rows)


# `topics` 와 달리 변경 통지(`on_change`)가 없다 -- 이 목록에서 파생되는 캐시가 하나도 없기 때문이고,
# 그것이 색인 캐시가 이 목록을 안 무는 이유와 같다(질의 축은 색인을 만들지 않는다).
_active: QueryStopwords | None = None


def use(words: QueryStopwords) -> QueryStopwords:
    """이 목록을 프로세스의 활성 목록으로 세운다. 주제 사전과 같은 이유로 전역이다 --
    `bm25.tokenize_query` 아래로 커넥션을 들고 다닐 자리가 없다(`topics` 의 주석)."""
    global _active
    _active = words
    return words


def forget() -> None:
    global _active
    _active = None


def active() -> QueryStopwords:
    """세우지 않은 것과 활성 버전이 없는 것은 **같은 상태**다 -- 둘 다 필터 없는 검색이고, 그것이
    이 목록 이전의 검색이다. 그래서 주제 사전과 달리 여기서 멈추지 않는다."""
    return _active if _active is not None else NONE


def use_active(conn: Any) -> QueryStopwords:
    return use(load(conn))


def query_note(query: str) -> str | None:
    """이 질의에서 뭘 뺐는지. 뺀 것이 없으면 None -- `pipeline.coverage_note` 와 같은 모양이고,
    같은 이유로 stderr 로 나가며 종료 코드를 바꾸지 않는다(멈춰야 할 일이 아니다)."""
    from analysis.retrieval.bm25 import tokenize

    words = active()
    tokens = tokenize(query)
    dropped = words.dropped(tokens)
    if not dropped or words.keep(tokens) == tokens:
        return None
    at = f"v{words.version}" if words.version is not None else "없음"
    return f"질의 불용어({at})로 뺀 토큰: {dropped} -- 색인은 그대로다"
