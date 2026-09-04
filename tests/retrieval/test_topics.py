"""That the topic dictionary comes from the active version of `needs.aspect_lexicon`, and that the
dictionary that came that way is **the same dictionary** as the constant version (#8).

Equivalence is the subject of this file. The measured search table of `contracts/interfaces.md` (six mode x
engine lines) is the value the coordinator remeasured and settled on 2026-08-26, and those numbers stand on
the answers `gold_from_chunks` -> `match_topics` made. Let the topic set differ by one alias and that table
quietly becomes an old table.
"""

from __future__ import annotations

import psycopg
import pytest
from sqlalchemy.engine import make_url

from analysis.retrieval import bm25, topics
from analysis.retrieval import eval as retrieval_eval
from cosmai.cli import main
from tests.retrieval import frozen_topics
from tests.retrieval.conftest import csv_topics, install_topics
from tests.retrieval.test_lexicon_v3 import LISTED, expected_entries

# The text matched against the constant version. Every alias plus the boundaries the slice demo() held
# (coupang false hits, particles, inflected forms).
CORPUS = [
    "",
    "백탁없이 촉촉하게 발려요",
    "SPF50+ PA++++ 제품입니다",
    "구매링크 https://link.coupang.com/abc",
    "징크 베이스 무기자차 제품",
    "산화아연 20% 함유",
    "재구매 의사 있어요",
    "눈 시림이 심하고 눈따가워요",
    "톤 업 되는 메이크업베이스",
    "UVA UVB 둘 다 막아줍니다",
    "avobenzone 들어간 케미컬 선크림",
    "지속적으로 쓰고 있어요",
    "땀에 강하고 워터프루프",
]
CORPUS += [alias for entry in frozen_topics.TOPICS for alias in entry["ko"] + entry["latin"]]


def _connect(url: str) -> psycopg.Connection:
    parsed = make_url(url)
    return psycopg.connect(
        host=parsed.host,
        port=parsed.port,
        user=parsed.username,
        password=parsed.password,
        dbname=parsed.database,
        options=parsed.query["options"],  # pyright: ignore[reportArgumentType]
    )


@pytest.fixture
def conn(needs_schema: str, needs_runtime_url: str):
    connection = _connect(needs_runtime_url)
    try:
        yield connection
    finally:
        connection.close()


def _same_dictionary(loaded: topics.Topics) -> None:
    """What it is matched against is the frozen v1 **plus the decision ledger of fork #56**. It is not v1 as
    it stands because #56 added seven aliases, and that what was added is exactly that ledger is carried by
    `test_lexicon_v3.py` -- swap the constant version wholesale here and nobody can read "what grew when"."""
    assert [e["topic"] for e in loaded.entries] == [e["topic"] for e in frozen_topics.TOPICS]
    for got, frozen in zip(loaded.entries, expected_entries(), strict=True):
        assert got["ko"] == frozen["ko"], got["topic"]
        assert got["latin"] == frozen["latin"], got["topic"]
        assert got["topic_type"] == frozen["topic_type"], got["topic"]
        assert got["trend_use"] == frozen["trend_use"], got["topic"]
        assert got["note"] == frozen["note"], got["topic"]
        # mfds_inci 는 집합으로 맞댄다: 한 주제 안에서 같은 말이 ko 와 mfds_inci 에 둘 다 있어
        # (아보벤존·옥토크릴렌·자외선차단제) 행 하나가 두 계열을 겸하고, 그 행의 자리는 ko 순서다.
        # 이 열은 매칭에도 질의에도 쓰이지 않으므로 순서에 뜻이 없다.
        assert set(got["mfds_inci"]) == set(frozen["mfds_inci"]), got["topic"]


def test_the_repo_csv_is_the_frozen_dictionary_plus_the_ledger():
    """`dict/topics_v1.csv` is the load source -- drift here from the constant version plus the ledger, and
    the dictionary that goes into the DB is neither the dictionary that made the measured table nor one that
    can say which delta that table sits on."""
    _same_dictionary(csv_topics())


def test_matching_agrees_with_the_frozen_constant():
    for text in CORPUS:
        for excluded in (False, True):
            assert topics.match_topics(text, include_excluded=excluded) == frozen_topics.match_topics(
                text, include_excluded=excluded
            ), text


def test_the_queries_and_the_expansion_words_are_the_ones_the_constant_gave():
    """The evaluation queries and the token expansion list are derived from the dictionary -- one of the three
    out of step and the query counts of the measured table (literal 61 · heldout 60) change."""
    frozen_queries = {
        mode: [
            (entry["topic"], alias)
            for entry in expected_entries()
            if entry["trend_use"]
            for alias in entry["ko"] + entry["latin"]
            if not (mode == "heldout" and len(entry["ko"] + entry["latin"]) < 2)
        ]
        for mode in ("literal", "heldout")
    }
    assert retrieval_eval.queries("literal") == frozen_queries["literal"]
    assert retrieval_eval.queries("heldout") == frozen_queries["heldout"]
    assert len(frozen_queries["literal"]) == 63  # v1's 61 + the two #56 added to judged topics
    assert bm25.expand_words() == sorted(
        {a for e in expected_entries() for a in e["ko"] if " " not in a and len(a) >= 2}
    )


def test_the_fingerprint_follows_the_aliases():
    """It is the value the index cache signature bites -- if it does not move when an alias changes, the old
    index is reused as it is."""
    before = csv_topics().fingerprint
    changed = topics.from_rows(
        [("백탁", "허옇", {"term_kind": "ko", "topic_type": "attribute", "trend_use": "true"})], 1
    )
    assert changed.fingerprint != before
    assert csv_topics().fingerprint == before  # same content, same signature (the cache key must be
    # deterministic)


def test_a_row_without_a_kind_is_refused():
    # Treated as ko quietly, an MFDS ingredient name slips into the substring matching and the matching
    # widens.
    with pytest.raises(ValueError, match="term_kind"):
        topics.from_rows([("백탁", "백탁", {"topic_type": "attribute", "trend_use": "true"})], 1)


def test_a_topic_that_says_two_types_is_refused():
    rows = [
        ("백탁", "백탁", {"term_kind": "ko", "topic_type": "attribute", "trend_use": "true"}),
        ("백탁", "하얗게", {"term_kind": "ko", "topic_type": "formula"}),
    ]
    with pytest.raises(ValueError, match="백탁"):
        topics.from_rows(rows, 1)


def test_a_topic_with_no_trend_use_is_refused():
    # With a default in place, a topic that ought to drop out of the evaluation queries comes in quietly
    # (sunscreen 481/518).
    with pytest.raises(ValueError, match="trend_use"):
        topics.from_rows([("백탁", "백탁", {"term_kind": "ko", "topic_type": "attribute"})], 1)


@pytest.mark.postgres
def test_the_active_version_is_what_the_lexicon_cli_loaded(conn, needs_runtime_url: str):
    """There is one load path, `cosmai lexicon load` -- with the search holding a loader of its own, a
    dictionary change goes without a version again."""
    argv = ["lexicon", "load", "--kind", "aspect", "--version", "1"]
    assert main([*argv, str(topics.DICTIONARY_CSV), "--url", needs_runtime_url]) == 0
    activate = ["lexicon", "activate", "--kind", "aspect", "--version", "1", "--url", needs_runtime_url]
    assert main(activate) == 0
    loaded = topics.load(conn)
    assert loaded.version == 1
    _same_dictionary(loaded)


@pytest.mark.postgres
def test_a_loaded_version_does_not_move_the_dictionary_until_it_is_activated(conn, needs_runtime_url: str):
    from db.lexicon import insert_aspects
    from tests.retrieval.conftest import csv_rows

    install_topics(conn)
    before = topics.load(conn)
    with conn.cursor() as cur:
        more = ("백탁", "generic", "", "허옇", False, topics.RULESET, 1, {"term_kind": "ko"})
        wider = [*csv_rows(), more]
        insert_aspects(cur, wider, 2, active=False)
    conn.commit()
    assert topics.load(conn).fingerprint == before.fingerprint
    activate = ["lexicon", "activate", "--kind", "aspect", "--version", "2", "--url", needs_runtime_url]
    assert main(activate) == 0
    after = topics.load(conn)
    assert after.version == 2
    assert "허옇" in {e["topic"]: e["ko"] for e in after.entries}["백탁"]
    assert after.fingerprint != before.fingerprint


@pytest.mark.postgres
def test_a_schema_with_no_active_topic_rows_refuses_instead_of_matching_nothing(conn):
    """An empty dictionary makes 0 answers and 0 queries with no error -- that green is indistinguishable from
    "the search finds nothing". It stops and says what to fix as well."""
    with pytest.raises(LookupError, match="cosmai lexicon"):
        topics.load(conn)


@pytest.mark.postgres
def test_the_polarity_ruleset_is_not_read_as_a_topic(conn):
    """Several rulesets live in one aspect dictionary version -- read the regexes of the polarity dictionary
    as topic aliases and `match_topics` hits any sentence at all."""
    from db.lexicon import insert_aspects

    install_topics(conn)
    with conn.cursor() as cur:
        insert_aspects(cur, [("효과없음", "generic", "", "효과|도움", True, "p1-v2.2", 1, {})], 1)
    conn.commit()
    assert [e["topic"] for e in topics.load(conn).entries] == [e["topic"] for e in frozen_topics.TOPICS]


# ---------- the revision string (fork #62) ----------


def test_the_stamp_names_the_ruleset_the_version_and_the_content():
    """The number alone is not a revision -- rows can be added to a version that is switched on and the number
    stays (the same reason `index_signature` bites the fingerprint as well)."""
    stamped = csv_topics(3).stamp
    assert stamped == (
        "ruleset=retrieval-topic · version=3 · topics=15 · aliases=80"
        f" · fingerprint={csv_topics(3).fingerprint}"
    )
    # The alias count counts only the two families the matching and the queries see. Counting mfds_inci with
    # them makes one word speak on two axes.
    assert csv_topics(3).aliases == 80
    # A bare load source has no number -- the number is given by the load. Filling it with 0 or 1 makes that
    # place a lie.
    assert "version=미적재" in csv_topics(None).stamp


def _pre_v3_loading_source() -> topics.Topics:
    """It takes only the seven rows of the #56 ledger out of the current load source and rebuilds the load
    source just before it."""
    entries = []
    for current in csv_topics().entries:
        entry = {key: list(value) if isinstance(value, list) else value for key, value in current.items()}
        for row in LISTED:
            if row.place == entry["topic"]:
                entry[row.kind].remove(row.term)
        entries.append(entry)
    return topics.Topics(entries=tuple(entries), version=1, fingerprint=topics._fingerprint(entries))


def test_the_pre_v3_loading_source_retraces_the_baseline_content_fingerprint():
    """Even with no old DB v1 rows, the content fingerprint of that load source is retraced to the same value
    as the remaining DB v2. The frozen copy is a proxy differing by one mfds_inci order, so its fingerprint
    must not be used as the baseline."""
    retraced = _pre_v3_loading_source()
    assert retraced.stamp == (
        "ruleset=retrieval-topic · version=1 · topics=15 · aliases=73 · fingerprint=5a0cae76311e1408"
    )
    frozen = topics.Topics(
        entries=tuple(frozen_topics.TOPICS),
        version=1,
        fingerprint=topics._fingerprint(frozen_topics.TOPICS),
    )
    assert frozen.stamp == (
        "ruleset=retrieval-topic · version=1 · topics=15 · aliases=73 · fingerprint=4afd3b25522a4d26"
    )
    assert topics.differences(retraced, frozen) == ["≈ 유기자차.mfds_inci: 순서만 다르다"]


def test_the_difference_between_two_dictionaries_names_the_axis_it_is_on():
    """The fingerprint says only that they differ. Unable to say what differs, "the fingerprints differ" reads
    as "the scores differ" -- and in fact the frozen copy and the load source differ by the **order** of a
    column the matching does not see (fork #62)."""
    frozen = topics.Topics(
        entries=tuple(frozen_topics.TOPICS),
        version=1,
        fingerprint=topics._fingerprint(frozen_topics.TOPICS),
    )
    lines = topics.differences(csv_topics(3), frozen)
    assert "≈ 유기자차.mfds_inci: 순서만 다르다" in lines
    # The seven #56 added come out as four (three topics x families). No other axis differs.
    assert sorted(line.split(":")[0] for line in lines) == [
        "~ 선크림.ko",
        "~ 선크림.latin",
        "~ 촉촉함_건조함.ko",
        "~ 톤업_메이크업베이스.ko",
        "≈ 유기자차.mfds_inci",
    ]
    assert topics.differences(csv_topics(3), csv_topics(9)) == []  # the number is not dictionary content
