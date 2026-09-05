"""The list of unmatched expressions (#8).

A dictionary cannot say by itself what it fails to catch -- an ingredient or a formulation outside the
dictionary is not observed at all, by the search or by the trend judgement, and that fact shows up in no
number. This list is the only place that puts that ceiling in front of a person, so what is held here is
"what drops out of the candidates": a word the dictionary already catches, and a general word that is common
in the control group too.
"""

from __future__ import annotations

import psycopg
import pytest
from sqlalchemy.engine import make_url

from analysis.retrieval import terms
from tests.retrieval.conftest import install_topics

pytestmark = pytest.mark.postgres

# 주제가 걸린 문서 6건에만 나오는 말(병원)과, 어디에나 나오는 말(사람)을 같이 넣는다.
TOPICAL = [f"백탁이 심해서 병원 다녀왔다 사람들 조심하세요 {i}" for i in range(6)]
CONTROL = [f"사람들이 많이 사는 물건이다 {i}" for i in range(6)]
INGREDIENT = ["에칠헥실트리아존 들어간 제품", "티타늄디옥사이드 함유", "구매링크 https://link.coupang.com/x"]


@pytest.fixture
def corpus(needs_schema: str, needs_runtime_url: str):
    parsed = make_url(needs_runtime_url)
    conn = psycopg.connect(
        host=parsed.host,
        port=parsed.port,
        user=parsed.username,
        password=parsed.password,
        dbname=parsed.database,
        options=parsed.query["options"],  # pyright: ignore[reportArgumentType]
    )
    install_topics(conn)
    with conn.cursor() as cur:
        for i, text in enumerate([*TOPICAL, *CONTROL, *INGREDIENT]):
            doc = f"d{i:03d}"
            cur.execute(
                "INSERT INTO retrieval_chunk (chunk_id, doc_id, source, ordinal, text, text_md5) "
                "VALUES (%s, %s, 'youtube_comment', 0, %s, 'x')",
                (f"{doc}#0", doc, text),
            )
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


def test_a_noun_the_dictionary_already_catches_is_not_a_candidate(corpus):
    # A word that is in the dictionary left among the candidates makes the list a copy of the dictionary
    # rather than its "ceiling".
    found = {row.term for row in terms.unmatched(terms.scan(corpus))}
    assert "백탁" not in found
    assert "병원" in found


def test_a_word_that_is_just_as_common_outside_the_topics_is_not_a_candidate(corpus):
    """빈도만으로 뽑으면 상위가 피부·제품·사람으로 채워진다 -- 선크림이라서 많은 말이 아니라
    한국어라서 많은 말이다(ydc 실측). 대조군 대비 비중으로 거른다."""
    found = {row.term for row in terms.unmatched(terms.scan(corpus))}
    assert "사람" not in found


def test_every_dictionary_term_gets_a_row_even_when_it_never_appears(corpus):
    """A count of 0 is kept too -- that an MFDS ingredient name does not appear on YouTube is the ground for
    needing a mapping."""
    rows = {(row.topic, row.term): row for row in terms.ingredients(terms.scan(corpus))}
    assert rows[("유기자차", "에칠헥실트리아존")].docs == 1
    assert rows[("무기자차", "티타늄디옥사이드")].docs == 1
    assert rows[("무기자차", "산화아연")].docs == 0
    assert rows[("유기자차", "에칠헥실트리아존")].term_kind == "mfds_inci"


def test_a_latin_term_keeps_its_boundary_match(corpus):
    # Counted as a substring, PA hits coupang and 16% false positives go straight into the table.
    rows = {(row.topic, row.term): row for row in terms.ingredients(terms.scan(corpus))}
    assert rows[("SPF_PA", "PA")].docs == 0


def test_the_report_says_how_to_put_a_term_into_the_dictionary(corpus):
    # The list is the input a person puts into the dictionary -- without the way in written down, everyone
    # invents their own.
    rendered = terms.render(terms.scan(corpus))
    assert "cosmai lexicon" in rendered
    assert terms.DICTIONARY_CSV.name in rendered


# ---------- the default source set is the text sources; the ledger is opt-in (#84) ----------


@pytest.fixture
def with_a_filing(corpus):
    """The ledger beside the text: one `mfds` chunk carrying the same ingredient sentence as a text
    document, so whether it was walked shows in the document count and in that term's count."""
    from analysis.retrieval import corpus as sources

    with corpus.cursor() as cur:
        cur.execute(
            "INSERT INTO retrieval_chunk (chunk_id, doc_id, source, ordinal, text, text_md5) "
            "VALUES ('f1#0', 'f1', %s, 0, %s, 'z')",
            (sources.MFDS, INGREDIENT[0]),
        )
    corpus.commit()
    return corpus


def test_terms_scans_the_text_sources_unless_the_ledger_is_asked_for(with_a_filing):
    """The report exists to grow the topic dictionary from consumer speech, and a filing's item and company
    names are not speech. Fork #77 added `mfds` to `corpus.SOURCES` and the default scan widened to five
    sources without a word (shk95-1/cosmai#235 finding 2); the default is the encoded text sources now,
    like `embed`, and `--source mfds` opts the ledger in."""
    from analysis.retrieval import corpus as sources

    text_documents = len(TOPICAL) + len(CONTROL) + len(INGREDIENT)
    by_default = terms.scan(with_a_filing)
    assert sum(by_default.documents.values()) == text_documents
    everything = terms.scan(with_a_filing, sources=sources.SOURCES)
    assert sum(everything.documents.values()) == text_documents + 1
    ledger_only = terms.scan(with_a_filing, sources=(sources.MFDS,))
    assert sum(ledger_only.documents.values()) == 1
    # The same term is counted once from the text and twice with the ledger in.
    term = next(t for (_topic, t) in by_default.term_docs if t in INGREDIENT[0])
    assert by_default.term_docs[next(k for k in by_default.term_docs if k[1] == term)] == 1
    assert everything.term_docs[next(k for k in everything.term_docs if k[1] == term)] == 2
