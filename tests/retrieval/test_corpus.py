"""The source adapters. A cursor out of step with the natural key makes rows vanish quietly at a page
boundary, so BATCH is lowered to force several real pages and the whole set is counted."""

from __future__ import annotations

from datetime import date

import psycopg
import pytest
from sqlalchemy.engine import make_url

from analysis.retrieval import corpus

pytestmark = pytest.mark.postgres


def _connect(url: str, schema: str) -> psycopg.Connection:
    parsed = make_url(url)
    conn = psycopg.connect(
        host=parsed.host,
        port=parsed.port,
        user=parsed.username,
        password=parsed.password,
        dbname=parsed.database,
        options=f"-csearch_path={schema},pg_catalog",
    )
    conn.autocommit = True
    return conn


def _seed_youtube(conn: psycopg.Connection, n: int) -> None:
    with conn.cursor() as cur:
        for i in range(n):
            cur.execute(
                "INSERT INTO comments (video_id, comment_id, text, is_hearted_by_uploader, "
                "is_pinned, published_at, first_seen_at, last_seen_at) "
                "VALUES (%s, %s, %s, false, false, %s, now(), now())",
                (f"v{i // 3:03d}", f"c{i:03d}", f"백탁 {i}", date(2026, 1, 1)),
            )
            cur.execute(
                "INSERT INTO video_snapshots (artifact_id, video_id, fetched_at, title, "
                "published_at) VALUES (%s, %s, now(), %s, %s)",
                (f"a{i:03d}", f"v{i:03d}", f"제목 {i}", date(2026, 1, 1)),
            )


def test_every_comment_survives_the_page_boundary(tubedepth_schema, _schema_name, monkeypatch):
    monkeypatch.setattr(corpus, "BATCH", 3)  # 페이지가 여러 장 나와야 커서를 실제로 검사한다
    conn = _connect(tubedepth_schema, _schema_name)
    try:
        _seed_youtube(conn, 10)
        docs = list(corpus.youtube_comments(conn, _schema_name))
    finally:
        conn.close()
    assert len(docs) == 10
    assert len({d.doc_id for d in docs}) == 10
    assert docs[0].source == corpus.YOUTUBE_COMMENT
    assert docs[0].doc_id.startswith("youtube_comment:")


def test_only_the_latest_snapshot_of_a_video_becomes_a_document(tubedepth_schema, _schema_name):
    conn = _connect(tubedepth_schema, _schema_name)
    try:
        with conn.cursor() as cur:
            for artifact, title, when in (("a1", "옛 제목", "2026-01-01"), ("a2", "새 제목", "2026-02-01")):
                cur.execute(
                    "INSERT INTO video_snapshots (artifact_id, video_id, fetched_at, title) "
                    "VALUES (%s, 'v1', %s, %s)",
                    (artifact, when, title),
                )
        docs = list(corpus.youtube_videos(conn, _schema_name))
    finally:
        conn.close()
    assert [d.text for d in docs] == ["새 제목"]


def test_since_filters_by_the_source_date(tubedepth_schema, _schema_name):
    conn = _connect(tubedepth_schema, _schema_name)
    try:
        with conn.cursor() as cur:
            for comment_id, published in (("c1", "2026-01-01"), ("c2", "2026-06-01")):
                cur.execute(
                    "INSERT INTO comments (video_id, comment_id, text, is_hearted_by_uploader, "
                    "is_pinned, published_at, first_seen_at, last_seen_at) "
                    "VALUES ('v1', %s, '백탁', false, false, %s, now(), now())",
                    (comment_id, published),
                )
        docs = list(corpus.youtube_comments(conn, _schema_name, since=date(2026, 3, 1)))
    finally:
        conn.close()
    assert [d.doc_id for d in docs] == ["youtube_comment:c2"]


def test_a_commerce_review_doc_id_carries_the_site(trend_radar_schema, _schema_name):
    # review_key is unique only inside a site. Drop the site and reviews from two sites become one document.
    conn = _connect(trend_radar_schema, _schema_name)
    try:
        with conn.cursor() as cur:
            for source in ("oliveyoung", "hwahae"):
                cur.execute(
                    "INSERT INTO review (source, review_key, captured_at, product_key, body, "
                    "written_at) VALUES (%s, 'r1', now(), 'p1', '백탁', now())",
                    (source,),
                )
        docs = list(corpus.commerce_reviews(conn, _schema_name))
    finally:
        conn.close()
    assert sorted(d.doc_id for d in docs) == [
        "commerce_review:hwahae:r1",
        "commerce_review:oliveyoung:r1",
    ]


# ---------- the MFDS ledger (#77) ----------
SNAPSHOT_LABEL = "mfds-ydc-v0.4.0"
REPORT_NO = 2018008612


def _seed_mfds(conn: psycopg.Connection, seqs: tuple[int, ...]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO mfds_snapshot (snapshot_id, label, source_tag, source_file, source_rows, "
            "max_report_date, update_policy) VALUES (1, %s, 'ydc v0.4.0', 'eval/mfds/x.csv', %s, %s, "
            "'not_updated')",
            (SNAPSHOT_LABEL, len(seqs), date(2026, 8, 20)),
        )
        for i, seq in enumerate(seqs):
            cur.execute(
                "INSERT INTO mfds_registration (report_seq, item_name, entp_name, report_date, "
                "entp_key, snapshot_id) VALUES (%s, %s, 'acme labs', %s, 'acmelabs', 1)",
                (seq, f"sun cream {i}", date(2026, 8, 20)),
            )


def test_a_filing_is_one_document_that_names_the_snapshot_it_came_from(needs_runtime_url, _schema_name):
    # The five parts in order: without the report number and the date in the text, a bm25 hit cannot
    # answer the two questions the ledger was brought in for (#74 rows 8 and 13).
    conn = _connect(needs_runtime_url, _schema_name)
    try:
        _seed_mfds(conn, (REPORT_NO,))
        docs = list(corpus.mfds_filings(conn, _schema_name))
    finally:
        conn.close()
    assert [d.doc_id for d in docs] == [f"mfds:{REPORT_NO}"]
    assert docs[0].source == corpus.MFDS
    assert docs[0].text == (
        f"sun cream 0 · acme labs · report no. {REPORT_NO} · registered 2026-08-20 · {SNAPSHOT_LABEL}"
    )


def test_every_filing_survives_the_page_boundary(needs_runtime_url, _schema_name, monkeypatch):
    # report_seq is a bigint where every other cursor in this module is text, and a cursor out of step
    # with its column loses rows at a page boundary instead of raising.
    monkeypatch.setattr(corpus, "BATCH", 2)
    seqs = tuple(REPORT_NO + i for i in range(5))
    conn = _connect(needs_runtime_url, _schema_name)
    try:
        _seed_mfds(conn, seqs)
        docs = list(corpus.mfds_filings(conn, _schema_name))
    finally:
        conn.close()
    assert [d.doc_id for d in docs] == [f"mfds:{seq}" for seq in seqs]
