"""주제 사전. BM25 의 토큰 확장과 검색 평가의 정답이 모두 여기서 나온다.

사전의 원천은 `needs.aspect_lexicon` 의 **활성 버전**(ruleset = `retrieval-topic`)이다. 리터럴로
이 파일에 얼어 있던 동안은 사전을 고쳐도 `cosmai lexicon load/diff/activate` 를 타지 않아 변경이
버전을 받지 못했다(포크 #8). 레포의 `dict/topics_v1.csv` 는 그 v1 의 적재 원본이고, 사전을 고치는
길은 그 CSV 를 고쳐 **다음 버전으로 적재하고 켜는 것** 하나다.

**활성 사전은 프로세스 전역이다.** `bm25.tokenize` 는 색인 한 벌을 세우는 동안 청크마다 불리는데
그 아래로 커넥션을 들고 다닐 자리가 없다 -- DB 를 여는 입구(`pipeline.load_index` ·
`eval.gold_from_chunks` · `terms`)가 `use_active(conn)` 로 세우고, 그 아래는 `active()` 로 읽는다.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, LiteralString

RULESET = "retrieval-topic"
# 별칭의 표기 계열. ko 는 부분문자열, latin 은 경계 매칭이고 mfds_inci(식약처 성분명)는 매칭에
# 쓰지 않는다 -- 표기가 유튜브 말과 겹치지 않아(실측 0건) 넣으면 매칭이 아니라 잡음이 는다.
KINDS = ("ko", "latin", "mfds_inci")
DICTIONARY_CSV = Path(__file__).resolve().parent / "dict" / "topics_v1.csv"
FIX = f"`cosmai lexicon load --kind aspect --version <n> {DICTIONARY_CSV.name}` 뒤 `activate`"

ACTIVE_SQL: LiteralString = """
SELECT aspect, pattern, extra, version FROM aspect_lexicon
WHERE active AND ruleset = %s ORDER BY id
"""
VERSION_SQL: LiteralString = """
SELECT aspect, pattern, extra, version FROM aspect_lexicon
WHERE version = %s AND ruleset = %s ORDER BY id
"""


class NoDictionary(LookupError):
    """활성 주제 사전이 없다. 실패가 아니라 아직 적재하지 않은 것이라 CLI 에서는 blocked(2) 다 --
    벡터 저장소가 없는 것(vectors.StoreMissing)과 같은 자리다."""


_TRUE = frozenset({"true", "t", "yes", "1"})
_FALSE = frozenset({"false", "f", "no", "0"})


@dataclass(frozen=True)
class Topics:
    """사전 한 벌. `entries` 는 적재 순서를 지킨다 -- `match_topics` 가 그 순서로 답한다."""

    entries: tuple[dict, ...]
    version: int
    fingerprint: str
    _latin: dict[str, re.Pattern[str] | None] = field(default_factory=dict, repr=False)

    def latin(self, topic: str) -> re.Pattern[str] | None:
        return self._latin.get(topic)


def latin_pattern(terms: Sequence[str]) -> re.Pattern[str] | None:
    """영문 토큰은 앞뒤가 영문자가 아닐 때만 매칭한다 (coupang -> PA 오탐 16% 차단)."""
    if not terms:
        return None
    alts = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z]){alts}(?![A-Za-z])", re.IGNORECASE)


def _flag(value: Any, where: str) -> bool:
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueError(f"{where}: trend_use 는 참/거짓이어야 한다 -- {value!r}")


def _agree(entry: dict, key: str, value: Any, topic: str) -> None:
    """주제 단위 사실은 그 주제의 행 아무 데나 한 번 적으면 된다. 두 행이 다른 값을 말하면 어느
    쪽이 사전인지 알 수 없으므로 거절한다 -- 반쯤 고친 CSV 가 여기서 걸린다."""
    if value is None or value == "":
        return
    if entry[key] is not None and entry[key] != value:
        raise ValueError(f"주제 {topic!r} 의 {key} 가 두 값을 말한다: {entry[key]!r} vs {value!r}")
    entry[key] = value


def _fingerprint(entries: Iterable[Mapping[str, Any]]) -> str:
    """사전 내용 전부의 지문. 색인 캐시 서명이 이것을 문다(pipeline.index_signature).

    설명(note)까지 넣는다 -- 토큰을 바꾸지 않는 칸을 빼면 "사전이 바뀌면 캐시가 무효화된다"가
    예외를 가진 규칙이 되고, 그 예외는 사전을 고친 사람이 아니라 다음 사람이 밟는다."""
    payload = "\n".join(
        "|".join(
            [
                entry["topic"],
                entry["topic_type"],
                "1" if entry["trend_use"] else "0",
                ",".join(entry["ko"]),
                ",".join(entry["latin"]),
                ",".join(entry["mfds_inci"]),
                entry["note"],
            ]
        )
        for entry in entries
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def from_rows(rows: Iterable[tuple[str, str, Mapping[str, Any] | None]], version: int) -> Topics:
    """(aspect, pattern, extra) 행들을 사전 한 벌로. DB 와 적재 CSV 가 같은 함수를 탄다."""
    entries: dict[str, dict] = {}
    for aspect, pattern, extra in rows:
        spare = dict(extra or {})
        kinds = [k for k in str(spare.get("term_kind", "")).split("|") if k]
        where = f"{aspect} :: {pattern}"
        if not kinds:
            raise ValueError(f"{where}: term_kind 가 없다 ({'|'.join(KINDS)} 중 하나 이상)")
        if unknown := [k for k in kinds if k not in KINDS]:
            raise ValueError(f"{where}: 모르는 term_kind {unknown}")
        entry = entries.setdefault(
            aspect,
            {
                "topic": aspect,
                "topic_type": None,
                "ko": [],
                "latin": [],
                "mfds_inci": [],
                "trend_use": None,
                "note": None,
            },
        )
        for kind in kinds:
            entry[kind].append(pattern)
        _agree(entry, "topic_type", spare.get("topic_type"), aspect)
        _agree(entry, "note", spare.get("note"), aspect)
        trend = spare.get("trend_use")
        _agree(entry, "trend_use", None if trend in (None, "") else _flag(trend, where), aspect)
    if not entries:
        raise NoDictionary(f"활성 주제 사전이 비었다 (ruleset={RULESET!r}) -- {FIX}")
    for entry in entries.values():
        for key in ("topic_type", "trend_use"):
            if entry[key] is None:
                # 기본값을 정해 두면 평가에서 빠져야 할 주제가 조용히 들어온다(선크림 481/518=93%).
                raise ValueError(f"주제 {entry['topic']!r} 에 {key} 가 없다")
        entry["note"] = entry["note"] or ""
    ordered = tuple(entries.values())
    return Topics(
        entries=ordered,
        version=version,
        fingerprint=_fingerprint(ordered),
        _latin={e["topic"]: latin_pattern(e["latin"]) for e in ordered},
    )


def load(conn: Any, *, version: int | None = None) -> Topics:
    """활성 버전(또는 지정한 버전)의 주제 사전. 읽고 나서 커밋한다 -- 뒤이어 형태소 분석이 붙는다."""
    with conn.cursor() as cur:
        if version is None:
            cur.execute(ACTIVE_SQL, (RULESET,))
        else:
            cur.execute(VERSION_SQL, (version, RULESET))
        rows = cur.fetchall()
    conn.commit()
    if not rows:
        # 빈 사전은 오류 없이 정답 0건·질의 0개를 만들고, 그 초록은 "검색이 아무것도 못 찾는다"와
        # 구분되지 않는다. 어디를 고쳐야 하는지까지 말하고 멈춘다.
        at = "활성 버전" if version is None else f"v{version}"
        raise NoDictionary(f"aspect_lexicon 의 {at} 에 주제 사전이 없다 (ruleset={RULESET!r}) -- {FIX}")
    label = version if version is not None else max(row[3] for row in rows)
    return from_rows(((row[0], row[1], row[2]) for row in rows), label)


_active: Topics | None = None
_listeners: list[Callable[[Topics | None], None]] = []


def on_change(listener: Callable[[Topics | None], None]) -> None:
    """활성 사전이 갈릴 때 부를 것. 사전에서 파생된 캐시(bm25 의 Kiwi·확장 목록)가 여기 붙는다 --
    topics 가 bm25 를 import 하면 순환이라 방향을 뒤집었다."""
    _listeners.append(listener)


def use(dictionary: Topics) -> Topics:
    """이 사전을 프로세스의 활성 사전으로 세운다."""
    global _active
    changed = _active is None or _active.fingerprint != dictionary.fingerprint
    _active = dictionary
    if changed:
        for listener in _listeners:
            listener(dictionary)
    return dictionary


def forget() -> None:
    global _active
    _active = None
    for listener in _listeners:
        listener(None)


def active() -> Topics:
    if _active is None:
        raise NoDictionary(
            f"활성 주제 사전이 세워지지 않았다 -- DB 를 여는 쪽이 use_active(conn) 로 세운다 ({FIX})"
        )
    return _active


def use_active(conn: Any) -> Topics:
    return use(load(conn))


def match_topics(text: str, *, include_excluded: bool = False, dictionary: Topics | None = None) -> list[str]:
    """텍스트에 등장하는 주제 목록. 한 문서가 여러 주제에 걸릴 수 있다."""
    if not text:
        return []
    known = dictionary or active()
    lowered = text.lower()
    hits = []
    for entry in known.entries:
        if not entry["trend_use"] and not include_excluded:
            continue
        if any(term.lower() in lowered for term in entry["ko"]):
            hits.append(entry["topic"])
            continue
        pattern = known.latin(entry["topic"])
        if pattern and pattern.search(text):
            hits.append(entry["topic"])
    return hits
