"""`cosmai lexicon load/diff/activate`: 사전은 버전으로만 바뀐다 (formats.md, 에픽 판정 9)."""

from __future__ import annotations

from pathlib import Path

import pytest

from analysis.lexicon import load_aspects, load_lexicon
from cosmai.cli import main
from db import seed
from db.lexicon import diff
from db.seed._common import connect

pytestmark = pytest.mark.postgres

# 이미 v1 에 있는 표면 하나. 같은 버전으로 다시 밀어도 사전은 꿈쩍하지 않아야 한다.
BRAND_V1_AGAIN = """kind,canonical,surface,tier,source,note
brand,3CE,3CE,normal,manual,
"""
BRAND_V2 = """kind,canonical,surface,tier,source,note
brand,라네즈,라네즈,normal,manual,
brand,라네즈,LANEIGE,normal,manual,
brand,헤라,헤라,cooc_required,manual,흔한 단어
"""
ASPECT_V2 = """aspect,scope,category,pattern,is_neutral_noun,ruleset,priority
백탁,category,선블록,백탁|허옇,false,suncare-v2.3,0
건조,generic,,건조|당김,false,suncare-v2.3,1
"""
# 알려진 일곱 칸 밖의 열. 룰셋마다 필요한 사실이 달라 `extra` 로 간다 (021, formats.md).
ASPECT_SPARE = """aspect,scope,category,pattern,is_neutral_noun,ruleset,priority,term_kind,note
백탁,generic,,백탁,false,retrieval-topic,1,ko,실측 근거
백탁,generic,,하얘,false,retrieval-topic,1,ko,
"""


@pytest.fixture
def seeded(needs_runtime_url: str) -> str:
    seed.run_all(needs_runtime_url, only=("lexicon",))
    return needs_runtime_url


def _csv(tmp_path: Path, name: str, body: str) -> str:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return str(path)


def test_a_loaded_version_is_inert_until_it_is_activated(seeded: str, tmp_path: Path, capsys):
    csv = _csv(tmp_path, "brand_v2.csv", BRAND_V2)
    assert main(["lexicon", "load", "--kind", "brand", "--version", "2", csv, "--url", seeded]) == 0
    with connect(seeded) as conn:
        assert load_lexicon(conn).version == 1
        assert len(load_lexicon(conn, version=2).surfaces) == 3

    assert main(["lexicon", "activate", "--kind", "brand", "--version", "2", "--url", seeded]) == 0
    with connect(seeded) as conn:
        active = load_lexicon(conn)
    assert active.version == 2
    # ingredient 는 아직 v1 이 켜져 있다: activate 는 kind 하나만 갈아 끼운다.
    assert {s.kind for s in active.surfaces} == {"brand", "ingredient"}
    assert sum(1 for s in active.surfaces if s.kind == "brand") == 3
    assert active.surface_to_canonical["laneige"] == "라네즈"
    assert active.cooc_required == {"헤라"}


def test_reloading_the_same_version_changes_nothing(seeded: str, tmp_path: Path, capsys):
    csv = _csv(tmp_path, "brand_v2.csv", BRAND_V2)
    main(["lexicon", "load", "--kind", "brand", "--version", "2", csv, "--url", seeded])
    capsys.readouterr()
    assert main(["lexicon", "load", "--kind", "brand", "--version", "2", csv, "--url", seeded]) == 0
    assert "0 loaded, 3 already there" in capsys.readouterr().out
    with connect(seeded) as conn:
        assert len(load_lexicon(conn, version=2).surfaces) == 3
        assert len(load_lexicon(conn, version=1).surfaces) == 992


def test_an_aspect_version_loads_and_activates_by_ruleset(seeded: str, tmp_path: Path):
    csv = _csv(tmp_path, "aspect_v2.csv", ASPECT_V2)
    assert main(["lexicon", "load", "--kind", "aspect", "--version", "2", csv, "--url", seeded]) == 0
    assert main(["lexicon", "activate", "--kind", "aspect", "--version", "2", "--url", seeded]) == 0
    with connect(seeded) as conn:
        loaded = load_aspects(conn, "suncare-v2.3")
    assert loaded.version == 2
    assert [p.aspect for p in loaded.patterns] == ["백탁", "건조"]


def test_diff_names_what_the_new_version_adds_and_drops(seeded: str, tmp_path: Path, capsys):
    csv = _csv(tmp_path, "brand_v2.csv", BRAND_V2)
    main(["lexicon", "load", "--kind", "brand", "--version", "2", csv, "--url", seeded])
    capsys.readouterr()
    assert (
        main(["lexicon", "diff", "--kind", "brand", "--version", "2", "--against", "1", "--url", seeded]) == 0
    )
    out = capsys.readouterr().out
    assert "+ LANEIGE" in out
    assert "- 3CE" in out


def test_a_csv_whose_kind_column_disagrees_is_refused(seeded: str, tmp_path: Path, capsys):
    csv = _csv(tmp_path, "brand_v2.csv", BRAND_V2)
    assert main(["lexicon", "load", "--kind", "ingredient", "--version", "2", csv, "--url", seeded]) == 2
    assert "carries kind(s) brand, not ingredient" in capsys.readouterr().out


def test_reloading_version_1_leaves_the_seeded_dictionary_alone(seeded: str, tmp_path: Path, capsys):
    csv = _csv(tmp_path, "brand_v1.csv", BRAND_V1_AGAIN)
    assert main(["lexicon", "load", "--kind", "brand", "--version", "1", csv, "--url", seeded]) == 0
    assert "0 loaded, 1 already there" in capsys.readouterr().out
    with connect(seeded) as conn:
        lex = load_lexicon(conn)
    assert (lex.version, len(lex.surfaces)) == (1, 992)


def test_activating_a_version_that_was_never_loaded_is_refused(seeded: str, capsys):
    # SET active = (version = n) 은 없는 버전을 주면 그 kind 를 통째로 끈다 — 그 전에 막는다.
    assert main(["lexicon", "activate", "--kind", "brand", "--version", "9", "--url", seeded]) == 2
    assert "no rows at version 9" in capsys.readouterr().out
    with connect(seeded) as conn:
        assert load_lexicon(conn).version == 1


def test_the_aspect_diff_keeps_one_key_per_row(seeded: str):
    # UNIQUE 에 pattern 이 들어 있어서 aspect/scope/category 만으로는 70행이 55키로 뭉친다.
    with connect(seeded) as conn, conn.cursor() as cur:
        empty = diff(cur, "aspect", 1, 9)
        cur.execute("SELECT count(*) FROM aspect_lexicon WHERE version = 1")
        loaded = cur.fetchone()
    assert loaded == (70,)
    assert len(empty.added) == 70


def test_a_row_the_ddl_check_refuses_comes_back_as_blocked(seeded: str, tmp_path: Path, capsys):
    bad = _csv(tmp_path, "aspect_bad.csv", ASPECT_V2.replace("category,선블록", "정체불명,선블록"))
    assert main(["lexicon", "load", "--kind", "aspect", "--version", "2", bad, "--url", seeded]) == 2
    assert "aspect_lexicon_scope_check" in capsys.readouterr().out


def test_a_spare_csv_column_lands_in_extra_and_shows_up_in_a_diff(seeded: str, tmp_path: Path, capsys):
    """룰셋이 자기 어휘를 나르는 칸이라, 그 값이 바뀐 것도 사전 변경이다 -- diff 가 못 보면
    사람은 무엇이 달라졌는지 행 목록으로만 알게 된다."""
    csv = _csv(tmp_path, "aspect_spare.csv", ASPECT_SPARE)
    assert main(["lexicon", "load", "--kind", "aspect", "--version", "3", csv, "--url", seeded]) == 0
    with connect(seeded) as conn, conn.cursor() as cur:
        cur.execute("SELECT pattern, extra FROM aspect_lexicon WHERE version = 3 ORDER BY id")
        assert cur.fetchall() == [
            ("백탁", {"term_kind": "ko", "note": "실측 근거"}),
            # 빈 칸은 값이 아니라 무기입이다 -- 넣으면 "지정하지 않음"과 "빈 문자열"이 섞인다.
            ("하얘", {"term_kind": "ko"}),
        ]
    named = _csv(tmp_path, "aspect_extra.csv", ASPECT_SPARE.replace("term_kind", "extra"))
    # 그 이름의 열은 자기 자신 안에 들어간다 -- 조용히 버리는 대신 blocked 다.
    assert main(["lexicon", "load", "--kind", "aspect", "--version", "9", named, "--url", seeded]) == 2
    assert "'extra' column" in capsys.readouterr().out
    louder = ASPECT_SPARE.replace("실측 근거", "실측 근거 2026-08-26")
    assert (
        main(
            [
                "lexicon",
                "load",
                "--kind",
                "aspect",
                "--version",
                "4",
                _csv(tmp_path, "aspect_spare2.csv", louder),
                "--url",
                seeded,
            ]
        )
        == 0
    )
    with connect(seeded) as conn, conn.cursor() as cur:
        assert diff(cur, "aspect", 4, 3).changed == ("백탁 :: generic ::  :: 백탁",)


def test_the_topic_dictionary_is_not_read_as_a_polarity_aspect(seeded: str):
    """aspect 사전 한 버전에는 룰셋이 여럿 산다. 검색 유닛의 주제 별칭이 극성 쪽 사전으로 새면
    `하얘` 가 aspect 패턴이 되어 라벨이 조용히 넓어진다 (formats.md B4: 로더는 ruleset 으로 읽는다)."""
    from analysis.retrieval import topics
    from db.lexicon import activate, insert_aspects
    from tests.retrieval.conftest import csv_rows

    with connect(seeded) as conn, conn.cursor() as cur:
        before = len(load_aspects(conn, "suncare-v2.2").patterns)
        insert_aspects(cur, csv_rows(), 1)
        activate(cur, "aspect", 1)
        conn.commit()
        assert len(load_aspects(conn, "suncare-v2.2").patterns) == before
        assert topics.load(conn).version == 1
