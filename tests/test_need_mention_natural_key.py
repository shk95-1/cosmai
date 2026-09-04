"""The natural key of need_mention -- a long sentence does not pass the btree cap, and extractor_version is in
the key (005).

Two defects demanded the same surgery. The first production run of #5 died with `index row size 3336 exceeds
btree version 4 maximum 2704 for index "need_mention_src_ref_need_key_sentence_key"` (an unbounded `sentence`
went straight into the btree key), and with no version in the key the seed rows and the analysis rows fought
over the same place.
"""

from __future__ import annotations

import hashlib
from typing import Any

import psycopg
import pytest

from db.seed._common import connect

pytestmark = pytest.mark.postgres

# 3200B, the same order as the 3336B that blew up in production. btree compresses a repeated string so it
# would not pass the cap -- the entropy has to be high; it is the band of the real shape where a review with
# no sentence break became one whole "sentence".
LONG_SENTENCE = "".join(hashlib.sha256(str(i).encode()).hexdigest() for i in range(50))
INSERT = (
    "INSERT INTO need_mention (src, site, ref, need_key, polarity, observed_at,"
    " observed_at_resolution, month, sentence, extractor_version, polarity_version)"
    " VALUES ('review', 'oliveyoung', %s, '발림성', '불만', '2026-03-04', 'day', '2026-03', %s, %s, %s)"
)


def _insert(url: str, ref: str, sentence: str, extractor: str, polarity_version: str = "rule-v2.2") -> None:
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute(INSERT, (ref, sentence, extractor, polarity_version))
        conn.commit()


def _rows(url: str, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute(query, params)  # type: ignore[arg-type]
        return cur.fetchall()


def test_a_sentence_past_the_btree_row_limit_is_stored(needs_runtime_url: str):
    """The row that stopped #5. With the sentence itself in the key it hits the 2704B cap and the whole run
    fails."""
    assert len(LONG_SENTENCE.encode()) > 2704
    _insert(needs_runtime_url, "P1/LONG", LONG_SENTENCE, "rule-v2.2")
    assert _rows(needs_runtime_url, "SELECT count(*) FROM need_mention") == [(1,)]


def test_two_extractor_versions_of_the_same_sentence_both_survive(needs_runtime_url: str):
    """Option A: the version is in the key, so the seed (slice-*) and the analysis (rule-v*) do not fight over
    the same place."""
    for extractor, polarity_version in (("slice-suncare", "rule-v2.1"), ("rule-v2.2", "rule-v2.2")):
        _insert(needs_runtime_url, "P1/R1", "끈적여요", extractor, polarity_version)
    found = _rows(
        needs_runtime_url,
        "SELECT extractor_version FROM need_mention WHERE ref = 'P1/R1' ORDER BY extractor_version",
    )
    assert found == [("rule-v2.2",), ("slice-suncare",)]


def test_one_extractor_version_still_gets_one_row_per_sentence(needs_runtime_url: str):
    """The key has not been loosened -- the same version emitting the same sentence still collides."""
    _insert(needs_runtime_url, "P1/R1", "끈적여요", "rule-v2.2")
    with pytest.raises(psycopg.errors.UniqueViolation):
        _insert(needs_runtime_url, "P1/R1", "끈적여요", "rule-v2.2")


def test_the_natural_key_is_a_unique_index_upserts_can_name(needs_runtime_url: str):
    """ON CONFLICT matches only the same form as the index expression -- that form is pinned down here."""
    definition = _rows(
        needs_runtime_url,
        "SELECT indexdef FROM pg_indexes WHERE tablename = 'need_mention'"
        " AND schemaname = current_schema() AND indexdef LIKE '%%md5%%'",
    )
    assert len(definition) == 1
    assert "UNIQUE" in definition[0][0]
    assert "(src, ref, need_key, extractor_version, md5(sentence))" in definition[0][0]
