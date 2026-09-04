"""The list of unmatched expressions. The two tables that put the ceiling of the dictionary in front of a
person (fork #8; the rules of the slices `ydc/unmatched_terms.py` · `ydc/ingredient_terms.py` were carried
over).

  미포착 표현   활성 사전이 **못 잡는** 고빈도 명사. 사전 밖의 성분·제형은 검색에도 트렌드 판정에도
                아예 관측되지 않고, 그 사실은 어떤 점수로도 나타나지 않는다.
  성분·제형 표기 사전의 표기(ko·latin)와 식약처 성분명(mfds_inci)이 코퍼스에서 몇 문서에 나오는가.
                등장 0건도 남긴다 -- "식약처 표기가 유튜브에 없다"가 매핑이 필요하다는 근거다.

**빈도만으로는 쓸 수 없다.** ydc 가 그렇게 뽑았더니 상위가 피부·제품·감사·언니·구매로 채워졌다 --
선크림이라서 많은 말이 아니라 한국어라서 많은 말이다. 그래서 절대 빈도가 아니라 대조군 대비
비중(lift)으로 거른다. 여기서 두 군은 **사전이 가른다**: 주제가 하나라도 걸린 문서와 하나도 안
걸린 문서. 사전의 천장을 재는 목록이므로 그 경계가 곧 대조군이다.

불용어 목록은 두지 않는다 -- lift 가 일반어를 걷어내는 축이고, 손으로 관리하는 불용어 파일은
버전을 못 받는 두 번째 사전이 되어 이 이슈가 없앤 문제를 다시 만든다. **이 판단은 색인·추출 축의
것이다**: 질의를 서술하는 말은 df 로 갈리지 않아(`소비자` 289 < `백탁` 338) 다른 근거가 필요하고,
질의 축은 포크 #46 이 따로 판단한다.

같은 축에서 그 판단이 슬라이스의 사전 자산 넷을 처분한다(포크 #37). `lexicon.json` 의 `stopwords` 86 은
이 lift 축이 **대체**하고, `protected` 32 는 불용어 목록을 막으려고만 있던 것이라 막을 것이 없어
**폐기**다 -- 32 중 22 는 이미 `dict/topics_v1.csv`·`dict/user_dictionary.tsv` 의 표기이고(대소문자
무시 기준. 정확 일치는 18 이고 차이 넷은 `SPF`·`PA`·`UVA`·`UVB`), 남는 10 중 셋(`모공막힘` 5 ·
`케미컬` 3 · `olive영` 0)은 코퍼스에 사실상 없고 일곱은 사전 후보로 포크 #56 이 진다. 평면 사본 셋
(`seeds/stopwords_ko.txt` 30 · `seeds/protected_terms.txt` 36 · `seeds/term_aliases.csv` 8)도 같이
폐기한다 -- 셋은 `lexicon.json` 의 사본조차 아니고(겹침 7 · 17 · 2) 더 낡은 세대다. 별칭만 살아남되
목록이 아니라 버전을 받는 행으로 **가야 한다**(`needs.aspect_lexicon` · `needs.entity_lexicon`):
9개 중 자리가 있는 것은 셋뿐이고 **5종은 아직 미이전**이라, 옮기기 전에 슬라이스를 지우면 그 표면형이
사라진다(포크 #56 · 포크 #9 가 그 이슈에 blockedBy).

**Nothing goes into the dictionary automatically.** The tables are material a person reads before editing
`dict/topics_v1.csv`, and the one path from that CSV into the DB is `cosmai lexicon load/diff/activate`.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, LiteralString

from analysis.retrieval import bm25, topics
from analysis.retrieval.topics import DICTIONARY_CSV

NOUN_TAGS = frozenset({"NNG", "NNP", "SL"})  # common noun · proper noun · foreign word
MIN_LENGTH = 2  # 한 글자 명사는 잡음이 많다 (거·것·수)
MIN_DOCS = 5  # kept the same as the ydc sample criterion
MIN_LIFT = 2.0  # below this multiple against the control group it counts as a general word
PAGE = 2000  # the same scale as eval.GOLD_PAGE

SCAN_SQL: LiteralString = """
SELECT chunk_id, doc_id, text FROM retrieval_chunk
WHERE chunk_id > %s{source}
ORDER BY chunk_id
LIMIT %s
"""

TOPICAL, CONTROL = "topical", "control"


@dataclass(frozen=True)
class Unmatched:
    term: str
    lift: float
    topical_docs: int
    other_docs: int
    in_ingredient_dictionary: bool


@dataclass(frozen=True)
class Ingredient:
    topic: str
    topic_type: str
    term: str
    term_kind: str
    docs: int


@dataclass(frozen=True)
class Scan:
    """Everything one pass over the corpus produced. The two tables share one pass -- walking 380k chunks
    twice means running the morphological analysis twice."""

    dictionary: topics.Topics
    documents: Counter = field(default_factory=Counter)
    nouns: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    term_docs: Counter = field(default_factory=Counter)


def is_word(noun: str) -> bool:
    """자모 조각과 기호를 걸러낸다 -- 자모 반복(ㅠㅠ·ㅎㅎ)이 명사로 잡혀 상위에 올라온다."""
    return all("가" <= c <= "힣" or c.isascii() for c in noun)


def ingredient_words() -> frozenset[str]:
    """The surface forms of the Kiwi ingredient dictionary. An unmatched noun found here is the first
    candidate to add."""
    path = bm25.DICT_DIR / "ingredient_dictionary.tsv"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    return frozenset(line.split("\t")[0] for line in lines if line and not line.startswith("#"))


def _documents(conn: Any, sources: tuple[str, ...] | None) -> Iterator[tuple[str, str]]:
    """(doc_id, document body). Chunks are folded into documents and walked one keyset page at a time (the
    same way as eval.gold_from_chunks) -- walking 380k rows in one stream with a server cursor holds that
    transaction open until the morphological analysis ends, and needs_runtime's transaction_timeout (60s) is
    a cap on the total lifetime of a transaction."""
    narrow, params = "", ()
    if sources:
        narrow, params = " AND source = ANY(%s)", (list(sources),)
    cursor, current, pieces = "", None, []
    while True:
        with conn.cursor() as cur:
            cur.execute(SCAN_SQL.format(source=narrow), (cursor, *params, PAGE))  # noqa: S608
            rows = cur.fetchall()
        conn.commit()
        # chunk_id is `<doc_id>#<ordinal>` and '#' sorts below any alphanumeric, so the pieces of one
        # document are necessarily adjacent in chunk_id order -- which is why only one document is held at a
        # time and the rest is streamed out.
        for _chunk_id, doc_id, text in rows:
            if doc_id != current:
                if current is not None:
                    yield current, " ".join(pieces)
                current, pieces = doc_id, []
            pieces.append(text)
        if len(rows) < PAGE:
            if current is not None:
                yield current, " ".join(pieces)
            return
        cursor = rows[-1][0]


def _term_patterns(dictionary: topics.Topics) -> list[tuple[str, str, str, str, re.Pattern | None]]:
    """(topic, topic_type, term, term_kind, boundary pattern). A latin spelling is matched on boundaries --
    counted as a substring, PA hits coupang and 16% false positives go straight into the table (measured on
    ydc)."""
    out = []
    for entry in dictionary.entries:
        seen: dict[str, str] = {}
        for kind in topics.KINDS:
            for term in entry[kind]:
                seen[term] = f"{seen[term]}|{kind}" if term in seen else kind
        for term, kind in seen.items():
            pattern = topics.latin_pattern([term]) if term.isascii() else None
            out.append((entry["topic"], entry["topic_type"], term, kind, pattern))
    return out


def scan(conn: Any, sources: tuple[str, ...] | None = None) -> Scan:
    """One pass over the corpus produces the material for both tables. The dictionary is the active version
    of this DB."""
    dictionary = topics.use_active(conn)
    found = Scan(dictionary=dictionary)
    terms = _term_patterns(dictionary)
    covered: dict[str, bool] = {}
    for _doc_id, text in _documents(conn, sources):
        lowered = text.lower()
        hits = topics.match_topics(text, include_excluded=True, dictionary=dictionary)
        bucket = TOPICAL if hits else CONTROL
        found.documents[bucket] += 1
        for topic, _type, term, _kind, pattern in terms:
            if pattern.search(text) if pattern else term.lower() in lowered:
                found.term_docs[(topic, term)] += 1
        for noun in _nouns(text, dictionary, covered):
            found.nouns[bucket][noun] += 1
    return found


def _nouns(text: str, dictionary: topics.Topics, covered: dict[str, bool]) -> set[str]:
    """The unmatched nouns one document produces. However often it occurs inside the document it is counted
    once (document count first).

    분석기는 색인이 쓰는 바로 그 Kiwi 다 -- 사전 없이 돌리면 `백탁` 이 `백`+`탁` 으로 쪼개져
    목록이 통째로 쓸모없어진다."""
    out: set[str] = set()
    tokens: Any = bm25.kiwi().tokenize(text)
    for token in tokens:
        noun = token.form
        if token.tag not in NOUN_TAGS or len(noun) < MIN_LENGTH or noun in out:
            continue
        if noun.isdigit() or not is_word(noun):
            continue
        hit = covered.get(noun)
        if hit is None:
            # The decision is made with the same function as the main pipeline -- two copies of the alias
            # rule would drift apart.
            hit = bool(topics.match_topics(noun, include_excluded=True, dictionary=dictionary))
            covered[noun] = hit
        if not hit:
            out.add(noun)
    return out


def unmatched(scanned: Scan, *, top: int | None = None) -> list[Unmatched]:
    """The unmatched nouns by their share against the control group."""
    topical = max(1, scanned.documents[TOPICAL])
    control = max(1, scanned.documents[CONTROL])
    known = ingredient_words()
    rows = []
    for term, docs in scanned.nouns[TOPICAL].items():
        if docs < MIN_DOCS:
            continue
        other = scanned.nouns[CONTROL][term]
        # With nothing in the control group the denominator is 0 and lift becomes infinite. It is held at 1
        # (Laplace correction) so the ranking does not explode.
        lift = (docs / topical) / (max(other, 1) / control)
        if lift < MIN_LIFT:
            continue
        rows.append(Unmatched(term, round(lift, 2), docs, other, term in known))
    rows.sort(key=lambda r: (-r.lift, -r.topical_docs, r.term))
    return rows[:top] if top else rows


def ingredients(scanned: Scan) -> list[Ingredient]:
    """One row per spelling in the dictionary. A count of 0 is kept as well."""
    rows = [
        Ingredient(topic, topic_type, term, kind, scanned.term_docs[(topic, term)])
        for topic, topic_type, term, kind, _pattern in _term_patterns(scanned.dictionary)
    ]
    rows.sort(key=lambda r: (r.topic, -r.docs, r.term))
    return rows


def render(scanned: Scan, *, top: int = 40) -> str:
    """The two tables a person reads. Nothing is dropped to a file -- this list is a snapshot of a corpus
    that grows daily, so in the repo it goes stale and, above all, **it is mistaken for a dictionary**. Anyone
    who wants to keep it redirects it."""
    candidates = unmatched(scanned)
    lines = [
        f"문서 {scanned.documents[TOPICAL]:,}건에 주제가 걸렸고 {scanned.documents[CONTROL]:,}건은 안 걸렸다"
        f" (사전 v{scanned.dictionary.version} · 주제 {len(scanned.dictionary.entries)}개)",
        "",
        f"미포착 표현 {len(candidates):,}종 (문서 {MIN_DOCS}건 이상 · lift {MIN_LIFT} 이상)"
        f" -- 상위 {min(top, len(candidates))}종",
        f"{'명사':<18}{'lift':>7}{'주제군':>8}{'대조군':>8}  성분사전",
    ]
    for row in candidates[:top]:
        mark = "있음" if row.in_ingredient_dictionary else ""
        lines.append(f"{row.term:<18}{row.lift:>7.1f}{row.topical_docs:>8,}{row.other_docs:>8,}  {mark}")
    lines += ["", "성분·제형 표기의 등장 문서 수 (0건도 남긴다 -- 없다는 사실이 근거다)"]
    lines.append(f"{'주제':<16}{'표기':<28}{'계열':<10}{'문서':>8}")
    for row in ingredients(scanned):
        lines.append(f"{row.topic:<16}{row.term:<28}{row.term_kind:<10}{row.docs:>8,}")
    lines += [
        "",
        f"자동으로 사전에 넣지 않는다. 넣을 말을 고르면 `{DICTIONARY_CSV.name}` 에 행을 더해",
        "`cosmai lexicon load --kind aspect --version <다음 버전>` -> `diff` -> `activate` 로 올린다.",
    ]
    return "\n".join(lines)
