"""The two rules of RuleLinker (p3 brand linking, p2 product matching) and the idempotence of `analyze link`.

The pure-function fixtures are rows lifted from the evaluation sets (eval/brand_link, eval/product_match) as
they are.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from sqlalchemy import create_engine

from analysis import predictors, registry
from analysis.lexicon import compile_lexicon
from analysis.linker import LINKER_VERSION, RuleLinker, accepts, normalize_name, normalized
from analysis.linker.evaluators import BrandLinkPredictor, ProductMatchPredictor
from analysis.linker.pipeline import BATCH, TABLES, run
from analysis.registry import LabeledRow, register, unregister
from analysis.types import EntitySurface, Lexicon, ProductRow, TextUnit
from cosmai.cli import main
from db import seed
from db.seed._common import connect

DUMPS = Path(__file__).resolve().parents[1] / "contracts" / "ddl" / "current"

# One small dictionary shows all four p3 rules: longest first · stop · cooc_required · a kind that is not
# brand.
SURFACES = (
    EntitySurface("brand", "라네즈", "라네즈", "normal", None),
    EntitySurface("brand", "라네", "라네", "normal", None),
    EntitySurface("brand", "이야", "이야", "stop", None),
    EntitySurface("brand", "미샤", "미샤", "cooc_required", None),
    EntitySurface("ingredient", "ZINC_OXIDE", "징크", None, "paper_lexicon"),
)


def unit(text: str) -> TextUnit:
    from datetime import date

    return TextUnit(
        src="yt_comment",
        site="youtube",
        ref="v/c",
        text=text,
        observed_at=date(2026, 1, 1),
        observed_at_resolution="day",
    )


@pytest.fixture
def lexicon() -> Lexicon:
    return compile_lexicon(SURFACES, 1)


# ---------- link (p3) ----------
def test_the_longest_surface_wins_and_the_hit_carries_its_kind(lexicon: Lexicon):
    hits = RuleLinker().link(unit("라네즈 크림 좋아요"), lexicon)
    assert [(h.kind, h.canonical, h.surface) for h in hits] == [("brand", "라네즈", "라네즈")]
    assert hits[0].cooc is True


def test_a_stop_tier_brand_is_never_linked(lexicon: Lexicon):
    assert RuleLinker().link(unit("이야 이거 좋다"), lexicon) == []


def test_a_cooc_required_brand_needs_a_product_word_within_the_window(lexicon: Lexicon):
    assert RuleLinker().link(unit("미샤 좋아요"), lexicon) == []
    hits = RuleLinker().link(unit("미샤 쿠션 샀어요"), lexicon)
    assert [(h.canonical, h.cooc) for h in hits] == [("미샤", True)]


def test_an_ingredient_surface_is_linked_with_its_own_kind(lexicon: Lexicon):
    # (d) surface_re is not brand-only -- brand_mention has to filter by kind to measure brand precision.
    hits = RuleLinker().link(unit("징크 함량이 높아요"), lexicon)
    assert [(h.kind, h.canonical) for h in hits] == [("ingredient", "ZINC_OXIDE")]


def test_the_hit_span_points_at_the_surface_without_the_particle(lexicon: Lexicon):
    text = "저는 라네즈를 씁니다"
    hit = RuleLinker().link(unit(text), lexicon)[0]
    assert text[hit.start : hit.end] == "라네즈"


# ---------- name normalization · pair decision (p2) ----------
def test_brackets_volume_and_marketing_words_leave_the_normalized_name():
    assert (
        normalize_name("[화잘먹] 조선미녀 맑은쌀 선크림 50ml+20ml 증정 기획", "조선미녀") == "맑은쌀 선크림"
    )


def test_a_pair_the_v2_rule_adopts_and_one_it_refuses():
    olive = normalized("[화해1위/모공쫀쫀 팩폼] 션리 다시마 앰플 클렌징폼 120ml", "", "oliveyoung")
    glow = normalized("다시마 앰플 클렌징폼", "", "glowpick")
    assert accepts(olive, glow).ok is True
    # 변별 토큰(베드타임)이 한쪽에만 있고 용량 숫자가 어긋난다 — 같은 브랜드의 다른 제품이다.
    bedtime = normalized("존슨즈베이비 베드타임 오일 500ml (아로마향)", "", "oliveyoung")
    baby = normalized("존슨즈 베이비 오일 200 ml", "", "daisomall")
    assert accepts(bedtime, baby).ok is False


def test_match_products_groups_two_sites_into_one_ref_and_leaves_variants_empty():
    products = [
        ProductRow(
            "oliveyoung", "A0001", "[NEW] 힌스 누 글로우 화이트 쿠션 5 Colors 기획 (본품+리필)", "힌스"
        ),
        ProductRow("glowpick", "G7", "누 글로우 화이트 쿠션 [SPF40/PA+++]", "힌스"),
        ProductRow("oliveyoung", "A0002", "힌스 세컨 스킨 메쉬 매트 쿠션 21호", "힌스"),
    ]
    match = RuleLinker().match_products(products)
    assert [(r.product_ref, r.n_sites, r.linker_version) for r in match.refs] == [
        ("oy:A0001", 2, LINKER_VERSION)
    ]
    assert sorted((m.source, m.product_key, m.role) for m in match.members) == [
        ("glowpick", "G7", "member"),
        ("oliveyoung", "A0001", "primary"),
    ]
    assert match.variants == ()  # B3: product_variant is outside the scope of #2
    assert [(c.src_a, c.key_a, c.src_b, c.key_b, c.mutual) for c in match.candidates] == [
        ("oliveyoung", "A0001", "glowpick", "G7", True)
    ]
    assert {m.match_score for m in match.members} == {match.candidates[0].dice}


def test_the_ref_name_norm_is_the_anchors_normalized_name():
    products = [
        ProductRow(
            "oliveyoung", "A0001", "[NEW] 힌스 누 글로우 화이트 쿠션 5 Colors 기획 (본품+리필)", "힌스"
        ),
        ProductRow("glowpick", "G7", "누 글로우 화이트 쿠션 [SPF40/PA+++]", "힌스"),
    ]
    (ref,) = RuleLinker().match_products(products).refs
    assert ref.name_norm == "누 글로우 화이트 쿠션"  # T19: 링커가 반드시 낸다


# ---------- evaluation implementations (pure) ----------
def row(ref: str, gold: str, text: str) -> LabeledRow:
    task = "brand_link" if "/" in ref else "product_match"
    return LabeledRow(task=task, ref=ref, split="holdout", gold=gold, text=text, extra={})


def test_the_brand_link_predictor_answers_ok_only_where_its_own_circuit_links():
    surfaces = (
        EntitySurface("brand", "에뛰드", "에뛰드", "normal", None),
        EntitySurface("brand", "올리브영", "올리브영", "stop", "retailer"),
    )
    predict = BrandLinkPredictor(lexicon=compile_lexicon(surfaces, 1))
    rows = [
        row(
            "uniform:comment/a/에뛰드",
            "OK",
            "에뛰드 색조 개이쁘다... 그와중에 울지냐언니 얼굴살 개많이빠짐..",
        ),
        row("uniform:comment/b/올리브영", "OK(retailer)", "이 사람이랑 올리브영 가고싶음"),
    ]
    # The second is a retailer the dictionary switches off with stop, so our circuit does not link it -- we
    # lose recall and keep precision.
    assert list(predict(rows)) == ["OK", "FP"]


def test_the_product_match_predictor_answers_adopt_or_reject_not_the_gold_label():
    rows = [
        row(
            "v2:1",
            "Y",
            "oliveyoung:[화해1위/모공쫀쫀 팩폼] 션리 다시마 앰플 클렌징폼 120ml"
            " | glowpick:다시마 앰플 클렌징폼",
        ),
        row(
            "v2:36",
            "N",
            "oliveyoung:존슨즈베이비 베드타임 오일 500ml (아로마향) | daisomall:존슨즈 베이비 오일 200 ml",
        ),
    ]
    assert list(ProductMatchPredictor()(rows)) == ["Y", "N"]


# ---------- DB ----------
pytestmark_db = pytest.mark.postgres


def _apply_dump(engine: Any, dump: Path, schema: str, original: str) -> None:
    body = "\n".join(
        line
        for line in dump.read_text(encoding="utf-8").splitlines()
        if not line.startswith("\\restrict") and not line.startswith("\\unrestrict")
    )
    ddl = body.replace(f"CREATE SCHEMA {original};", "").replace(f"{original}.", f'"{schema}".')
    with engine.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        conn.exec_driver_sql(ddl)
        conn.exec_driver_sql(f'GRANT USAGE ON SCHEMA "{schema}" TO needs_runtime')
        conn.exec_driver_sql(f'GRANT SELECT ON ALL TABLES IN SCHEMA "{schema}" TO needs_runtime')


@pytest.fixture
def source_schemas(database_url_for_tests: str, _schema_name: str) -> Iterator[tuple[str, str]]:
    """Stands the two source schemas up from the contract dumps as they are. They are kept apart because both
    dumps carry alembic_version."""
    tail = hashlib.sha1(_schema_name.encode()).hexdigest()[:10]
    names = (f"tr_{tail}", f"td_{tail}")
    engine = create_engine(database_url_for_tests)
    with engine.begin() as conn:
        for name in names:
            conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
    _apply_dump(engine, DUMPS / "app.trend_radar.sql", names[0], "trend_radar")
    _apply_dump(engine, DUMPS / "app.tubedepth.sql", names[1], "tubedepth")
    try:
        yield names
    finally:
        with engine.begin() as conn:
            for name in names:
                conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        engine.dispose()


PRODUCTS = (
    ("oliveyoung", "A0001", "[NEW] 힌스 누 글로우 화이트 쿠션 5 Colors 기획 (본품+리필)", "힌스"),
    ("glowpick", "G7", "누 글로우 화이트 쿠션 [SPF40/PA+++]", "힌스"),
    ("oliveyoung", "A0002", "라네즈 워터뱅크 블루 히알루로닉 크림 50ml", "라네즈"),
    ("glowpick", "G8", "워터뱅크 블루 히알루로닉 크림", "라네즈"),
)
COMMENTS = (
    ("VID1", "C1", "라네즈 크림 진짜 좋아요 재구매합니다"),
    ("VID1", "C2", "이 영상 잘 봤어요"),
)


@pytest.fixture
def sources(database_url_for_tests: str, source_schemas: tuple[str, str]) -> tuple[str, str]:
    """Source rows are inserted as the schema owner (needs_migrator) -- needs_runtime can do nothing but
    SELECT."""
    commerce, youtube = source_schemas
    engine = create_engine(database_url_for_tests)
    with engine.begin() as conn:
        for source, key, name, brand in PRODUCTS:
            conn.exec_driver_sql(
                f'INSERT INTO "{commerce}".product '
                "(source, product_key, captured_at, name, brand, first_seen_at, last_seen_at) "
                "VALUES (%s, %s, now(), %s, %s, '2026-01-05', now())",
                (source, key, name, brand),
            )
        conn.exec_driver_sql(
            f'INSERT INTO "{youtube}".video_snapshots '
            "(artifact_id, video_id, fetched_at, title, published_at) "
            "VALUES ('a1', 'VID1', now(), '라네즈 크림 리뷰', '2026-01-10')"
        )
        conn.exec_driver_sql(
            f'INSERT INTO "{youtube}".transcripts '
            "(video_id, language, is_automatic, full_text, segment_count, fetched_at) "
            "VALUES ('VID1', 'ko', true, '오늘은 라네즈 크림을 발라볼게요', 3, now())"
        )
        for video_id, comment_id, text in COMMENTS:
            conn.exec_driver_sql(
                f'INSERT INTO "{youtube}".comments '
                "(video_id, comment_id, text, is_hearted_by_uploader, is_pinned, published_at, "
                "first_seen_at, last_seen_at) "
                "VALUES (%s, %s, %s, false, false, '2026-01-11', now(), now())",
                (video_id, comment_id, text),
            )
    engine.dispose()
    return commerce, youtube


SEED_REF = "oy:A0001"


def _seed_rows(conn: psycopg.Connection[Any]) -> None:
    """The slice rows the seed left. A table with the version in the key has to coexist; one without it is
    refreshed by the recomputation."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO product_ref (product_ref, brand, name_norm, name, n_sites, linker_version) "
            "VALUES (%s, '힌스', 'old norm', 'old name', 1, 'slice-p2')",
            (SEED_REF,),
        )
        cur.execute(
            "INSERT INTO product_member (source, product_key, product_ref, role, match_score) "
            "VALUES ('oliveyoung', 'A0001', %s, 'primary', NULL)",
            (SEED_REF,),
        )
        cur.execute(
            "INSERT INTO product_ref_candidate "
            "(src_a, key_a, src_b, key_b, brand, dice, mutual, linker_version) "
            "VALUES ('oliveyoung', 'A0001', 'glowpick', 'G7', '힌스', 1.0, true, 'slice-p2')"
        )
        cur.execute(
            "INSERT INTO brand_mention (src, ref_id, brand, count, cooc_count, linker_version) "
            "VALUES ('comment', 'C1', '라네즈', 9, 9, 'slice-p3')"
        )
    conn.commit()


@pytest.mark.postgres
# batch=2 passes a batch boundary in all four tables -- a run split by commits has to give the same result as
# one run in a single lump.
@pytest.mark.parametrize("batch", [2, BATCH])
def test_analyze_link_writes_rows_and_a_second_run_changes_no_count(needs_runtime_url, sources, batch):
    commerce, youtube = sources
    seed.run_all(needs_runtime_url, only=("lexicon",))
    with connect(needs_runtime_url) as conn:
        _seed_rows(conn)
        first = run(conn, commerce_schema=commerce, youtube_schema=youtube, batch=batch)
        second = run(conn, commerce_schema=commerce, youtube_schema=youtube, batch=batch)
        with conn.cursor() as cur:
            cur.execute("SELECT linker_version, count(*) FROM brand_mention GROUP BY 1 ORDER BY 1")
            versions = cur.fetchall()
            cur.execute(
                "SELECT linker_version, name_norm FROM product_ref WHERE product_ref = %s", (SEED_REF,)
            )
            ref = cur.fetchone()
    assert first == second
    assert all(first[table] > 0 for table in TABLES)
    # A table with the version in its PK: the seed rows stay and a new version row is added.
    assert dict(versions)["slice-p3"] == 1
    assert dict(versions)[LINKER_VERSION] > 0
    # A table without the version in its PK: the same ref is a DO UPDATE (versioning.md).
    assert ref == (LINKER_VERSION, "누 글로우 화이트 쿠션")


@pytest.mark.postgres
def test_since_leaves_the_older_documents_out(needs_runtime_url, sources):
    from datetime import date

    commerce, youtube = sources
    seed.run_all(needs_runtime_url, only=("lexicon",))
    with connect(needs_runtime_url) as conn:
        counts = run(conn, since=date(2026, 6, 1), commerce_schema=commerce, youtube_schema=youtube)
    assert counts["brand_mention"] == 0
    assert counts["product_ref"] > 0  # identification takes no window -- a cut catalogue makes other clusters


@pytest.fixture
def registered(needs_runtime_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """The default implementations read the dictionary from production without --url -- that one place is
    turned to the test URL."""
    monkeypatch.setattr(predictors, "LEXICON_URL", needs_runtime_url)
    register("brand_link", LINKER_VERSION, BrandLinkPredictor())
    register("product_match", LINKER_VERSION, ProductMatchPredictor())
    yield needs_runtime_url
    unregister("brand_link")
    unregister("product_match")
    # Without restoring it here, a file running later meets 'no registration' (exit 2) (#30).
    registry.load_implementations()


@pytest.mark.postgres
def test_both_eval_tasks_score_and_land_in_analysis_run(registered: str, capsys):
    seed.run_all(registered, only=("lexicon", "labeled"))
    assert main(["eval", "brand_link", "--url", registered]) == 0
    assert main(["eval", "product_match", "--url", registered]) == 0
    out = capsys.readouterr().out
    assert "n=120" in out and "n=40" in out
    with connect(registered) as conn, conn.cursor() as cur:
        cur.execute("SELECT note, versions FROM analysis_run ORDER BY run_id")
        runs = cur.fetchall()
    assert [note for note, _ in runs] == [
        f"eval:brand_link:{LINKER_VERSION}",
        f"eval:product_match:{LINKER_VERSION}",
    ]
    brand = runs[0][1]["scores"]["P3 120"]
    match = runs[1][1]["scores"]["P2 blind 40"]
    # The first pass only needs a score to come out. This number is the current rule, and a change means that
    # PR updates the baseline table.
    assert round(brand["P:OK"], 3) == 0.991
    assert (round(match["strict"], 3), round(match["변형허용"], 3)) == (0.769, 0.974)
