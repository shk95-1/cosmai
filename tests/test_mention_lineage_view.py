"""`needs.mention_lineage`: 지표 한 칸에서 그 값을 만든 언급들과 원문 발췌까지 (#144 경로 4·5a·5b).

A19 는 뒤집지 않는다. `need_mention`·`wish_mention` 은 `run_id` 를 갖지 않고, 집계가 고르는 모집단은
`extractor_version` 하나다(`analysis/aggregate/pipeline.py` 의 `load_needs`) — 이 뷰는 그 모집단과
칸의 축(`category`·`need_key`·`month`·`product_ref`)을 필터 가능한 컬럼으로 내놓을 뿐, 스스로
run 을 고르지 않는다.

원문은 발췌만 나간다(사용자 결정 2026-08-27). 이유는 anon 이 리뷰 본문을 못 봐서가 **아니다** —
운영에서 `postgrest_anon` 은 `trend_radar_reader` 의 멤버라 `trend_radar.review.body` 를 그대로 읽고,
`db/grants/postgrest_anon_needs.sql` 은 `needs` 스키마만 다스린다(코디네이터 실측 2026-08-27).
자르는 이유는 **이 뷰가 원문 전달 경로가 되지 않게** 하려는 것이다: 칸 하나에 언급 수천 건이 딸려
나오는 자리라, 발췌가 아니면 여기가 사실상의 원문 덤프 출구가 된다.
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

EXCERPT = 120  # 사용자 결정: 근거로 쓰기엔 충분하고 전문 재구성은 안 되는 길이.
LONG_SENTENCE = "백" * 200
LONG_BODY = "본" * 300

CAPTURED = datetime(2026, 8, 20, 3, 0, tzinfo=UTC)

# (src, site, ref, product_ref, source_product_key, category, need_key, polarity, month, sentence)
NEED_ROWS = (
    # 리뷰 언급 — 원문이 trend_radar.review 에 있다(경로 5a).
    ("review", "glowpick", "g:1/r:1", "p:라운드랩", None, "선케어", "백탁", "불만", "2026-07", LONG_SENTENCE),
    # 같은 칸의 둘째 언급. 칸 → 언급들은 1:N 이다.
    ("review", "glowpick", "g:1/r:2", None, "g:1", "선케어", "백탁", "만족", "2026-07", "덜 하얗다"),
    # 동의어. scope='all' 롤업만 canonical 로 접는다(A17) — 카테고리 칸은 raw need_key 다.
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
    # 댓글 언급 — 원문이 tubedepth.comments 에 있다(경로 5b).
    ("yt_comment", "youtube", "v-1/c-1", None, None, "선케어", "백탁", "불만", "2026-07", "백탁 심함"),
    # 원문에 못 닿는 갈래. 행은 남고 doc_found 가 거짓이라야 한다.
    ("yt_transcript", "youtube", "v-1/t-1", None, None, "선케어", "백탁", "중립", "2026-07", "자막 문장"),
    # 지워진 리뷰를 가리키는 언급 — 언급은 있고 원문이 없다.
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
                # 같은 review_key 를 가진 다른 사이트. site 를 안 걸면 두 행이 된다.
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
        # need_mention.product_ref 는 FK 다 — 카탈로그 행이 언급보다 먼저 있어야 한다.
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
    """뷰를 읽는 롤은 needs_runtime 이다 — 원천 표에는 직접 닿지 않고 뷰만 읽는 것이 설계다.

    컬럼 이름도 같은 연결에서 가져온다: needs_migrator 는 CONNECTION LIMIT 2 라, 테스트마다 엔진을
    하나 더 여는 것만으로 스위트가 연결을 다 쓴다.
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
    # 칸 = (scope, need_key, month, product_ref). 그 넷이 그대로 컬럼이라야 화면이 eq 로 좁힌다.
    row = rows[("need", "g:1/r:1")]
    assert row["extractor_version"] == "rule-v2.3"
    assert (row["category"], row["need_key"], row["month"]) == ("선케어", "백탁", "2026-07")
    # 제품 축의 값은 product_ref 이고, 없으면 source_product_key 다 (aggregate/__init__.py 의 _product).
    assert row["product_axis"] == "p:라운드랩"
    assert rows[("need", "g:1/r:2")]["product_axis"] == "g:1"
    assert rows[("need", "v-1/c-1")]["product_axis"] == ""


def test_only_the_rollup_key_is_folded_to_canonical(rows: dict[tuple[str, str], Any]):
    # A17: scope='all' 롤업만 needs.need_key.canonical 로 접는다. 두 값이 한 행에 나란히 있어야
    # 카테고리 칸(raw)과 롤업 칸(canonical)이 같은 뷰에서 갈린다.
    synonym = rows[("need", "g:1/r:3")]
    assert synonym["need_key"] == "화이트캐스트"
    assert synonym["need_key_rollup"] == "백탁"
    plain = rows[("need", "g:1/r:1")]
    assert (plain["need_key"], plain["need_key_rollup"]) == ("백탁", "백탁")


def test_the_sentence_leaves_as_a_120_character_excerpt_only(rows: dict[tuple[str, str], Any]):
    row = rows[("need", "g:1/r:1")]
    assert row["sentence_excerpt"] == LONG_SENTENCE[:EXCERPT]
    # 잘렸다는 사실 자체는 숨기지 않는다 — 전문 길이가 나란히 있어야 발췌인 줄 안다.
    assert row["sentence_chars"] == len(LONG_SENTENCE)


def test_no_column_carries_the_full_source_text(view: dict[str, Any]):
    """이 뷰는 원문 전달 경로가 되지 않는다 — 나가는 것은 발췌뿐이다.

    이름 셋을 막는 것만으로는 `sentence_full` 같은 컬럼이 그대로 통과한다. 재는 것은 이름이 아니라
    **값**이다: 어떤 text 컬럼도 120자를 넘지 않는다. 픽스처가 200자 문장과 300자 본문을 일부러
    싣고 있어(LONG_SENTENCE·LONG_BODY) 자르지 않는 컬럼이 하나라도 생기면 여기서 걸린다.
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
    # ref 는 product_key/review_key 다. site 를 같이 걸지 않으면 다른 사이트의 같은 review_key 가 붙는다.
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
    # 조용히 사라지면 "칸이 이만큼을 셌다" 와 화면의 목록 길이가 어긋나고, 어느 쪽이 맞는지 알 수 없다.
    missing = rows[("need", "g:1/r:404")]
    assert missing["doc_found"] is False
    assert missing["doc_excerpt"] is None
    # 자막·블로그는 원문 표 자체가 없다. 못 닿는 것과 지워진 것을 doc_kind 가 가른다.
    transcript = rows[("need", "v-1/t-1")]
    assert transcript["doc_found"] is False
    assert transcript["doc_kind"] is None


def test_a_wish_row_carries_the_metrics_wish_axes(rows: dict[tuple[str, str], Any]):
    row = rows[("wish", "v-1/c-2")]
    assert row["wish_class"] == "a"
    # format 은 ';' 로 최대 3개가 들어오고 첫 번째가 주 값이다 (A12, aggregate/__init__.py 의 _first).
    assert row["format_first"] == "스틱"
    assert row["attribute_first"] == "무기자차"
    assert row["brand"] == "라운드랩"
    assert row["like_count"] == 12
    # 축이 빈 언급은 그 축의 marginal 행에만 들고, 값 있는 칸에는 끼지 않는다.
    assert rows[("wish", "v-1/c-3")]["format_first"] == ""
    # wish 는 polarity 축이 없다 — 없는 값을 '중립' 같은 것으로 채우지 않는다.
    assert row["polarity"] is None


def test_a_wish_comment_reaches_its_source_row_too(rows: dict[tuple[str, str], Any]):
    row = rows[("wish", "v-1/c-2")]
    assert (row["doc_kind"], row["doc_parent"], row["doc_key"]) == ("yt_comment", "v-1", "c-2")
    assert row["doc_found"] is True


# --- 배포 경로: db/migrate.sh 가 실제로 남기는 것 (tool/checks/test 의 throwaway 컨테이너) ---


@pytest.fixture
def deployed() -> Any:
    url = os.environ.get("TEST_POSTGRES_URL") or pytest.skip("set TEST_POSTGRES_URL, or run tool/checks/test")
    engine = create_engine(url)
    with engine.connect() as conn:
        conn.execute(text("SET ROLE needs_owner"))
        yield conn
    engine.dispose()  # needs_migrator 는 CONNECTION LIMIT 2 다 — 통과든 실패든 놓아준다.


def test_the_deploy_leaves_the_view_readable_by_the_screen(deployed: Any):
    """뷰의 권한은 뷰 파일이 진다(#158): 배포가 뷰를 DROP+CREATE 하므로 화이트리스트의 GRANT 는
    새 객체에 따라오지 않는다. 화면은 PostgREST 에 anon 으로 묻는다."""
    assert deployed.execute(text("SELECT to_regclass('needs.mention_lineage')")).scalar_one() is not None
    for role in ("needs_runtime", "postgrest_anon"):
        granted = deployed.execute(
            text("SELECT has_table_privilege(:r, 'needs.mention_lineage', 'SELECT')"), {"r": role}
        ).scalar_one()
        assert granted, role
