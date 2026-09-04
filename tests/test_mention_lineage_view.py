"""`needs.mention_lineage`: from one metrics cell to the mentions that made its value and their original
excerpt (#144 paths 4, 5a, 5b).

This does not reverse A19. `need_mention`/`wish_mention` carry no `run_id`, and the one population
aggregation picks is `extractor_version` (`load_needs` in `analysis/aggregate/pipeline.py`) -- this view
only puts that population and the cell's axes (`category`, `need_key`, `month`, `product_ref`) out as
filterable columns, and never picks a run on its own.

Only an excerpt of the original text goes out (user decision 2026-08-27). The reason is **not** that
anon cannot see the review body -- in production `postgrest_anon` is a member of `trend_radar_reader`
and reads `trend_radar.review.body` straight through, and `db/grants/postgrest_anon_needs.sql` only
governs the `needs` schema (coordinator measured this 2026-08-27). The reason this truncates is
**to keep this view from becoming a channel for the original text**: this is a spot where thousands of
mentions ride out of one cell, and without an excerpt this would effectively become an exit for dumping
the original text.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.postgres

REPO_ROOT = Path(__file__).resolve().parents[1]
VIEW = REPO_ROOT / "db" / "views" / "mention_lineage.sql"

# User decision: a length that's enough to use as evidence but not enough to reconstruct the full text.
EXCERPT = 120
LONG_SENTENCE = "백" * 200
LONG_BODY = "본" * 300

CAPTURED = datetime(2026, 8, 20, 3, 0, tzinfo=UTC)

# (src, site, ref, product_ref, source_product_key, category, need_key, polarity, month, sentence)
NEED_ROWS = (
    # A review mention -- its original text lives in trend_radar.review (path 5a).
    ("review", "glowpick", "g:1/r:1", "p:라운드랩", None, "선케어", "백탁", "불만", "2026-07", LONG_SENTENCE),
    # A second mention of the same cell. A cell -> mentions is 1:N.
    ("review", "glowpick", "g:1/r:2", None, "g:1", "선케어", "백탁", "만족", "2026-07", "덜 하얗다"),
    # A synonym. Only a scope='all' rollup folds this to canonical (A17) -- the category cell is the raw
    # need_key.
    (
        "review",
        "glowpick",
        "g:1/r:3",
        "p:라운드랩",
        None,
        "선케어",
        "화이트캐스트",
        "불만",
        "2026-08",
        "하얗다",
    ),
    # A comment mention -- its original text lives in tubedepth.comments (path 5b).
    ("yt_comment", "youtube", "v-1/c-1", None, None, "선케어", "백탁", "불만", "2026-07", "백탁 심함"),
    # A branch that never reaches the original text. The row stands and doc_found must be false.
    ("yt_transcript", "youtube", "v-1/t-1", None, None, "선케어", "백탁", "중립", "2026-07", "자막 문장"),
    # A mention pointing at a deleted review -- the mention exists, the original text does not.
    ("review", "glowpick", "g:1/r:404", None, None, "선케어", "백탁", "불만", "2026-07", "사라진 리뷰"),
)

# (src, ref, video_id, wish_class, brand, format, attribute, sentence, like_count)
WISH_ROWS = (
    ("yt_comment", "v-1/c-2", "v-1", "a", "라운드랩", "스틱;쿠션", "무기자차", "스틱으로 내주세요", 12),
    ("yt_comment", "v-1/c-3", "v-1", "b", "", "", "", "리뷰 해주세요", 3),
)


def _seed_and_create_view(url: str, schema: str, td_schema: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            f'INSERT INTO "{schema}".review (source, review_key, captured_at, product_key, rating,'
            " body, written_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [
                ("glowpick", "r:1", CAPTURED, "g:1", 2.0, LONG_BODY, datetime(2026, 7, 3, tzinfo=UTC)),
                ("glowpick", "r:2", CAPTURED, "g:1", 5.0, "잘 발린다", datetime(2026, 7, 4, tzinfo=UTC)),
                ("glowpick", "r:3", CAPTURED, "g:1", 1.0, "하얗다", datetime(2026, 8, 1, tzinfo=UTC)),
                # A different site carrying the same review_key. Without filtering by site, this becomes
                # two rows.
                ("oliveyoung", "r:1", CAPTURED, "o:9", 4.0, "다른 사이트", None),
            ],
        )
        conn.exec_driver_sql(f'GRANT SELECT ON "{schema}".review TO needs_owner')
        conn.exec_driver_sql(f'GRANT USAGE ON SCHEMA "{td_schema}" TO needs_owner')
        conn.exec_driver_sql(f'GRANT SELECT ON "{td_schema}".comments TO needs_owner')
        conn.exec_driver_sql(
            f'INSERT INTO "{td_schema}".comments (video_id, comment_id, text, like_count,'
            " is_hearted_by_uploader, is_pinned, published_at, first_seen_at, last_seen_at)"
            " VALUES (%s, %s, %s, %s, false, false, %s, %s, %s)",
            [
                ("v-1", "c-1", "댓글 원문 " + LONG_BODY, 7, CAPTURED, CAPTURED, CAPTURED),
                ("v-1", "c-2", "스틱으로 내주세요", 12, CAPTURED, CAPTURED, CAPTURED),
                ("v-1", "c-3", "리뷰 해주세요", 3, CAPTURED, CAPTURED, CAPTURED),
            ],
        )
        conn.exec_driver_sql("SET ROLE needs_owner")
        # need_mention.product_ref is an FK -- the catalog row must exist before the mention.
        conn.exec_driver_sql(
            f'INSERT INTO "{schema}".product_ref (product_ref, brand, name_norm, name, linker_version)'
            " VALUES ('p:라운드랩', '라운드랩', '자작나무', '자작나무 선크림', 'rule-v1.0')"
        )
        conn.exec_driver_sql(
            f'INSERT INTO "{schema}".need_key (need_key, canonical) VALUES'
            " ('백탁', '백탁'), ('화이트캐스트', '백탁')"
        )
        conn.exec_driver_sql(
            f'INSERT INTO "{schema}".need_mention (src, site, ref, product_ref, source_product_key,'
            " category, need_key, polarity, observed_at, observed_at_resolution, month, sentence,"
            " extractor_version, polarity_version)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'day', %s, %s, 'rule-v2.3', 'rule-v2.2')",
            [
                (src, site, ref, pref, spk, cat, nk, pol, date(2026, 7, 1), month, sentence)
                for src, site, ref, pref, spk, cat, nk, pol, month, sentence in NEED_ROWS
            ],
        )
        conn.exec_driver_sql(
            f'INSERT INTO "{schema}".wish_mention (src, ref, video_id, observed_at,'
            " observed_at_resolution, month, wish_class, brand, format, attribute, sentence,"
            " like_count, extractor_version)"
            " VALUES (%s, %s, %s, %s, 'day', '2026-07', %s, %s, %s, %s, %s, %s, 'rule-v2.3')",
            [
                (src, ref, vid, date(2026, 7, 1), wc, brand, fmt, attr, sentence, likes)
                for src, ref, vid, wc, brand, fmt, attr, sentence, likes in WISH_ROWS
            ],
        )
        conn.exec_driver_sql(
            VIEW.read_text(encoding="utf-8")
            .replace("needs.", f'"{schema}".')
            .replace("trend_radar.", f'"{schema}".')
            .replace("tubedepth.", f'"{td_schema}".')
        )
    engine.dispose()


@pytest.fixture
def view(
    needs_schema: str,
    trend_radar_schema: str,
    tubedepth_side_schema: str,
    needs_runtime_url: str,
    _schema_name: str,
) -> dict[str, Any]:
    """The role reading the view is needs_runtime -- the design is that it never touches the source
    tables directly and only reads the view.

    The column names are also pulled from this same connection: needs_migrator has CONNECTION LIMIT 2,
    so opening one more engine per test alone would use up the suite's connections.
    """
    _seed_and_create_view(needs_schema, _schema_name, tubedepth_side_schema)
    engine = create_engine(needs_runtime_url)
    with engine.connect() as conn:
        found = conn.execute(text("SELECT * FROM mention_lineage ORDER BY kind, mention_id")).mappings().all()
        columns = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema = :s"
                    " AND table_name = 'mention_lineage'"
                ),
                {"s": _schema_name},
            ).all()
        }
    engine.dispose()
    return {"rows": {(r["kind"], r["ref"]): dict(r) for r in found}, "columns": columns}


@pytest.fixture
def rows(view: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return view["rows"]


def test_every_mention_gets_one_row_need_and_wish_in_the_same_shape(rows: dict[tuple[str, str], Any]):
    assert len([k for k in rows if k[0] == "need"]) == len(NEED_ROWS)
    assert len([k for k in rows if k[0] == "wish"]) == len(WISH_ROWS)


def test_the_cell_axes_are_filterable_columns(rows: dict[tuple[str, str], Any]):
    # A cell = (scope, need_key, month, product_ref). Those four must be columns as they stand for the
    # screen to narrow by eq.
    row = rows[("need", "g:1/r:1")]
    assert row["extractor_version"] == "rule-v2.3"
    assert (row["category"], row["need_key"], row["month"]) == ("선케어", "백탁", "2026-07")
    # The product axis's value is product_ref, or source_product_key if that is absent (_product in
    # aggregate/__init__.py).
    assert row["product_axis"] == "p:라운드랩"
    assert rows[("need", "g:1/r:2")]["product_axis"] == "g:1"
    assert rows[("need", "v-1/c-1")]["product_axis"] == ""


def test_only_the_rollup_key_is_folded_to_canonical(rows: dict[tuple[str, str], Any]):
    # A17: only a scope='all' rollup folds this through needs.need_key.canonical. Both values must sit
    # side by side in one row for the raw category cell and the rollup cell to split apart in the same
    # view.
    synonym = rows[("need", "g:1/r:3")]
    assert synonym["need_key"] == "화이트캐스트"
    assert synonym["need_key_rollup"] == "백탁"
    plain = rows[("need", "g:1/r:1")]
    assert (plain["need_key"], plain["need_key_rollup"]) == ("백탁", "백탁")


def test_the_sentence_leaves_as_a_120_character_excerpt_only(rows: dict[tuple[str, str], Any]):
    row = rows[("need", "g:1/r:1")]
    assert row["sentence_excerpt"] == LONG_SENTENCE[:EXCERPT]
    # The fact that it was cut is never hidden -- the full length has to sit next to it for a reader to
    # know it's an excerpt.
    assert row["sentence_chars"] == len(LONG_SENTENCE)


def test_no_column_carries_the_full_source_text(view: dict[str, Any]):
    """This view never becomes a channel for the original text -- only an excerpt ever goes out.

    Blocking just three names would still let a column like `sentence_full` through untouched. What
    this measures is not a name but a **value**: no text column ever exceeds 120 characters. The
    fixture deliberately carries a 200-character sentence and a 300-character body
    (LONG_SENTENCE, LONG_BODY), so any column that fails to truncate is caught right here.
    """
    assert {"sentence", "body", "text"} & view["columns"] == set()
    over = {
        (key, column): len(value)
        for key, row in view["rows"].items()
        for column, value in row.items()
        if isinstance(value, str) and len(value) > EXCERPT
    }
    assert not over, over


def test_a_review_mention_reaches_its_source_row_by_site_and_review_key(rows: dict[tuple[str, str], Any]):
    # ref is product_key/review_key. Without also filtering by site, the same review_key from a
    # different site attaches too.
    row = rows[("need", "g:1/r:1")]
    assert row["doc_found"] is True
    assert (row["doc_kind"], row["doc_parent"], row["doc_key"]) == ("review", "g:1", "r:1")
    assert row["doc_excerpt"] == LONG_BODY[:EXCERPT]
    assert row["doc_chars"] == len(LONG_BODY)
    assert row["doc_rating"] == 2.0
    assert row["doc_at"] == datetime(2026, 7, 3, tzinfo=UTC)


def test_a_comment_mention_reaches_its_source_row_by_video_and_comment_id(rows: dict[tuple[str, str], Any]):
    row = rows[("need", "v-1/c-1")]
    assert row["doc_found"] is True
    assert (row["doc_kind"], row["doc_parent"], row["doc_key"]) == ("yt_comment", "v-1", "c-1")
    assert row["doc_excerpt"].startswith("댓글 원문 ")
    assert row["doc_like_count"] == 7


def test_a_mention_whose_source_row_is_gone_keeps_its_row(rows: dict[tuple[str, str], Any]):
    # If it quietly vanished, "the cell counted this many" and the screen's list length would disagree,
    # with no way to tell which one is right.
    missing = rows[("need", "g:1/r:404")]
    assert missing["doc_found"] is False
    assert missing["doc_excerpt"] is None
    # A transcript or a blog has no original-text table at all. doc_kind tells "unreachable" apart from
    # "deleted".
    transcript = rows[("need", "v-1/t-1")]
    assert transcript["doc_found"] is False
    assert transcript["doc_kind"] is None


def test_a_wish_row_carries_the_metrics_wish_axes(rows: dict[tuple[str, str], Any]):
    row = rows[("wish", "v-1/c-2")]
    assert row["wish_class"] == "a"
    # format arrives as up to 3 values joined by ';' and the first is the primary value (A12,
    # _first in aggregate/__init__.py).
    assert row["format_first"] == "스틱"
    assert row["attribute_first"] == "무기자차"
    assert row["brand"] == "라운드랩"
    assert row["like_count"] == 12
    # A mention with an empty axis only lands in that axis's marginal row, never in a cell that has a
    # value.
    assert rows[("wish", "v-1/c-3")]["format_first"] == ""
    # wish 는 polarity 축이 없다 — 없는 값을 '중립' 같은 것으로 채우지 않는다.
    assert row["polarity"] is None


def test_a_wish_comment_reaches_its_source_row_too(rows: dict[tuple[str, str], Any]):
    row = rows[("wish", "v-1/c-2")]
    assert (row["doc_kind"], row["doc_parent"], row["doc_key"]) == ("yt_comment", "v-1", "c-2")
    assert row["doc_found"] is True


# --- Deploy path: what db/migrate.sh actually leaves behind (tool/checks/test's throwaway container) ---


@pytest.fixture
def deployed() -> Any:
    url = os.environ.get("TEST_POSTGRES_URL") or pytest.skip("set TEST_POSTGRES_URL, or run tool/checks/test")
    engine = create_engine(url)
    with engine.connect() as conn:
        conn.execute(text("SET ROLE needs_owner"))
        yield conn
    engine.dispose()  # needs_migrator has CONNECTION LIMIT 2 -- always release, pass or fail.


def test_the_deploy_leaves_the_view_readable_by_the_screen(deployed: Any):
    """A view's own file carries its grants (#158): since the deploy does DROP+CREATE on the view, a
    whitelist's GRANT never carries over to the new object. The screen asks PostgREST as anon."""
    assert deployed.execute(text("SELECT to_regclass('needs.mention_lineage')")).scalar_one() is not None
    for role in ("needs_runtime", "postgrest_anon"):
        granted = deployed.execute(
            text("SELECT has_table_privilege(:r, 'needs.mention_lineage', 'SELECT')"), {"r": role}
        ).scalar_one()
        assert granted, role
