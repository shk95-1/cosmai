"""The topic dictionary. Both BM25's token expansion and the answers of the search evaluation come from here.

The rules come from ydc `topics.py` (shk95-1/cosmai-ydc-old `v0.1.0` `02440ab`) and were written over rather
than imported from the pinned copy `analysis/slices/ydc/` (fork #9 deleted that copy).

The source of the dictionary is the **active version** of `needs.aspect_lexicon` (ruleset =
`retrieval-topic`). While it was frozen into this file as literals, editing the dictionary did not go through
`cosmai lexicon load/diff/activate` and the change was given no version (fork #8). The repo's
`dict/topics_v1.csv` is the load source of that v1, and the one way to edit the dictionary is to edit that
CSV and **load it as the next version and switch it on**.

**The active dictionary is process-global.** `bm25.tokenize` is called per chunk while one index is being
built and there is no place to carry a connection below it -- the entrances that open the DB
(`pipeline.load_index` · `eval.gold_from_chunks` · `terms`) set it with `use_active(conn)`, and everything
below reads it with `active()`.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, LiteralString

RULESET = "retrieval-topic"
# The spelling families of an alias. ko is a substring match, latin is a boundary match, and mfds_inci (the
# MFDS ingredient name) is not used for matching -- its spellings do not overlap YouTube speech (0 measured),
# so including it grows noise rather than matches.
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
    """There is no active topic dictionary. Not a failure but something not loaded yet, so in the CLI it is
    blocked(2) -- the same place as a missing vector store (vectors.StoreMissing)."""


_TRUE = frozenset({"true", "t", "yes", "1"})
_FALSE = frozenset({"false", "f", "no", "0"})


@dataclass(frozen=True)
class Topics:
    """One dictionary. `entries` keeps the load order -- `match_topics` answers in that order."""

    entries: tuple[dict, ...]
    version: int | None  # the number the load gives. A bare load-source CSV does not have one yet (fork #62)
    fingerprint: str
    _latin: dict[str, re.Pattern[str] | None] = field(default_factory=dict, repr=False)

    def latin(self, topic: str) -> re.Pattern[str] | None:
        return self._latin.get(topic)

    @property
    def aliases(self) -> int:
        """The number of spellings a query and the matching actually see. `mfds_inci` is not counted -- that
        family is used neither for matching nor in queries (KINDS above), so counting it together makes two
        numbers on different axes into one word."""
        return sum(len(entry["ko"]) + len(entry["latin"]) for entry in self.entries)

    @property
    def stamp(self) -> str:
        """One line saying which revision this dictionary is (fork #62). The same place as
        `vectors.VectorStore.stamp` -- just as an evaluation row writes down the store revision itself, the
        dictionary revision is written down by the dictionary itself.

        The number alone is not a revision: rows can be added to a version that is switched on and the number
        stays (the same reason `pipeline.index_signature` bites the fingerprint as well). So the fingerprint
        travels with it.
        """
        label = "미적재" if self.version is None else self.version
        return (
            f"ruleset={RULESET} · version={label} · topics={len(self.entries)}"
            f" · aliases={self.aliases} · fingerprint={self.fingerprint}"
        )


def latin_pattern(terms: Sequence[str]) -> re.Pattern[str] | None:
    """A latin token matches only when neither side is a letter (blocks the coupang -> PA 16% false hits)."""
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
    """A topic-level fact needs writing once on any row of that topic. When two rows say different values
    there is no telling which is the dictionary, so it is refused -- a half-edited CSV is caught here."""
    if value is None or value == "":
        return
    if entry[key] is not None and entry[key] != value:
        raise ValueError(f"주제 {topic!r} 의 {key} 가 두 값을 말한다: {entry[key]!r} vs {value!r}")
    entry[key] = value


def _fingerprint(entries: Iterable[Mapping[str, Any]]) -> str:
    """The fingerprint of the entire dictionary content. The index cache signature bites this
    (pipeline.index_signature).

    Even the note goes in -- leaving out a column that does not change the tokens turns "the cache is
    invalidated when the dictionary changes" into a rule with an exception, and that exception is stepped on
    by the next person rather than by the one who edited the dictionary."""
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


def from_rows(rows: Iterable[tuple[str, str, Mapping[str, Any] | None]], version: int | None) -> Topics:
    """(aspect, pattern, extra) rows into one dictionary. The DB and the load CSV go through the same
    function."""
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
                # With a default in place, a topic that ought to drop out of the evaluation comes in quietly
                # (sunscreen 481/518=93%).
                raise ValueError(f"주제 {entry['topic']!r} 에 {key} 가 없다")
        entry["note"] = entry["note"] or ""
    ordered = tuple(entries.values())
    return Topics(
        entries=ordered,
        version=version,
        fingerprint=_fingerprint(ordered),
        _latin={e["topic"]: latin_pattern(e["latin"]) for e in ordered},
    )


COMPARED = ("topic_type", "trend_use", "ko", "latin", "mfds_inci", "note")


def differences(left: Topics, right: Topics) -> list[str]:
    """**On which axis** two dictionaries differ. One line = one column of one topic (fork #62).

    The fingerprint says only that they differ, not where. `cosmai lexicon diff` cannot answer it either --
    that side is per row, so a changed alias order in one topic looks like several rows changed wholesale.
    What differs here is the compiled dictionary, and **a column that differs only in order is written as
    such**: `_fingerprint` bites the order of a column the matching never sees, such as `mfds_inci` (that is
    the rule -- the comment above), so the revision looks changed while the score cannot move with that
    column.
    """
    ours = {entry["topic"]: entry for entry in left.entries}
    theirs = {entry["topic"]: entry for entry in right.entries}
    out = [f"+ 주제 {topic}" for topic in ours if topic not in theirs]
    out += [f"- 주제 {topic}" for topic in theirs if topic not in ours]
    if set(ours) == set(theirs) and list(ours) != list(theirs):
        # The entry order is the input order of the fingerprint (`_fingerprint`). Same content, different
        # fingerprint.
        out.append("≈ 주제 순서만 다르다")
    for topic, entry in ours.items():
        other = theirs.get(topic)
        if other is None:
            continue
        for key in COMPARED:
            mine, yours = entry[key], other[key]
            if mine == yours:
                continue
            if isinstance(mine, list) and sorted(mine) == sorted(yours):
                out.append(f"≈ {topic}.{key}: 순서만 다르다")
            else:
                out.append(f"~ {topic}.{key}: {mine!r} vs {yours!r}")
    return out


def load(conn: Any, *, version: int | None = None) -> Topics:
    """The topic dictionary of the active version (or a named one). Committed after the read -- morphological
    analysis follows right after."""
    with conn.cursor() as cur:
        if version is None:
            cur.execute(ACTIVE_SQL, (RULESET,))
        else:
            cur.execute(VERSION_SQL, (version, RULESET))
        rows = cur.fetchall()
    conn.commit()
    if not rows:
        # An empty dictionary makes 0 answers and 0 queries with no error, and that green is
        # indistinguishable from "the search finds nothing". It stops and says what to fix as well.
        at = "활성 버전" if version is None else f"v{version}"
        raise NoDictionary(f"aspect_lexicon 의 {at} 에 주제 사전이 없다 (ruleset={RULESET!r}) -- {FIX}")
    label = version if version is not None else max(row[3] for row in rows)
    return from_rows(((row[0], row[1], row[2]) for row in rows), label)


_active: Topics | None = None
_listeners: list[Callable[[Topics | None], None]] = []


def on_change(listener: Callable[[Topics | None], None]) -> None:
    """What to call when the active dictionary changes. The caches derived from the dictionary (bm25's Kiwi
    and its expansion list) hang off here -- topics importing bm25 would be a cycle, so the direction was
    turned around."""
    _listeners.append(listener)


def use(dictionary: Topics) -> Topics:
    """Makes this dictionary the process's active dictionary."""
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
    """The topics that appear in the text. One document can hit several topics."""
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
