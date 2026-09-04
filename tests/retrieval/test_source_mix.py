"""소스별 분배를 `ranked_chunks` 에 **더하지 않는다**는 결정을 지키는 자리 (포크 #54).

ydc `rag/engine.py` 는 소스마다 따로 뽑아 합친다 -- 색인의 92%가 짧은 유튜브 댓글이라 전역 상위 k 가
`mfds` 를 293위로, `ingredient` 를 300위 밖으로 밀어냈기 때문이다. 그 조건이 우리에게도 있는지를 잰
것이 이 이슈이고, 답은 **없다** 였다. 지킬 것이 셋이다.

**① 판정 기준은 재기 전에 정해졌다.** 세 갈래(`쏠리지 않는다`·`밀리지 않는다`·`지배한다`)와 두 상수
(`K`·`BURIED_RANK`)가 숫자를 보기 전에 고정됐다. 결과를 보고 기준을 만드는 것이 이 측정이 막으려는
일이라, 기준이 조용히 움직이면 판정도 조용히 움직인다 (`test_vector_floor` 와 같은 자리).

**(2) The absence of an allocation is a decision, not something unimplemented.** `ranked_chunks` only
narrows the candidates by `sources` and emits the global top k of what is left. The day that property changes
the contract's §Per-source allocation has to change with it, so it is caught here.

**(3) Is the number that decision quoted still true.** The way to measure it is `tool/measure-source-mix`,
and it needs the production DB and the 380,000-chunk index, so this suite does not call it (the same place as
the six lines of §Retrieval measurements). What is held here is **the table's shape and the constants**, and
the numbers themselves live in the contract.
"""

from __future__ import annotations

from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from types import ModuleType

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[2]
INTERFACES = ROOT / "contracts" / "interfaces.md"
ENTRYPOINTS = ROOT / "contracts" / "entrypoints.md"
TOOL = ROOT / "tool" / "measure-source-mix"
HEADER = "## 소스별 분배"


def loaded() -> ModuleType:
    """It has no extension, so a plain import does not reach it (the same way as
    `test_vector_floor.loaded`)."""
    spec = spec_from_loader("measure_source_mix", SourceFileLoader("measure_source_mix", str(TOOL)))
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def section() -> str:
    body = INTERFACES.read_text(encoding="utf-8")
    start = body.index(HEADER)
    return body[start : body.index("\n## ", start)]


def test_the_three_verdicts_were_fixed_before_any_number_was_seen():
    mix = loaded()
    # If the dominant source's share of the top k falls short of its share of the index, ydc's condition does
    # not exist at all -- our measurement has this shape.
    assert mix.verdict(0.7564, 0.7051, [19, 32, 4]) == mix.NO_SKEW
    # Even when skewed, a minority source sitting near k does not justify the loss distribution costs.
    assert mix.verdict(0.7564, 0.90, [19, 32, 4]) == mix.NOT_BURIED
    # Skewed with the minority source unable to take a place is the same condition as ydc's.
    assert mix.verdict(0.7564, 0.90, [293, 300, 431]) == mix.DOMINATED
    # With no minority source holding a candidate, it can be called neither pushed out nor not pushed out.
    assert mix.verdict(0.7564, 0.90, []) == mix.UNMEASURABLE


def test_the_boundaries_are_the_ones_the_criteria_named():
    """With nobody keeping the inequality, the suite stays green when `<` becomes `<=`."""
    mix = loaded()
    # A share **equal** to the composition means "it follows the composition as it is", so that is skew.
    assert mix.verdict(0.7564, 0.7564, [4]) != mix.NO_SKEW
    # A median equal to the threshold is pushed out -- the threshold is "pushed out from here on".
    assert mix.verdict(0.7564, 0.90, [mix.BURIED_RANK]) == mix.DOMINATED
    assert mix.verdict(0.7564, 0.90, [mix.BURIED_RANK - 1]) == mix.NOT_BURIED


def test_the_median_carries_the_verdict_not_the_worst_query():
    """Judging by the 777th place of one query gives dominance on any corpus -- the tail is always long."""
    mix = loaded()
    assert mix.verdict(0.7564, 0.90, [1, 2, 777]) == mix.NOT_BURIED


def test_the_composition_is_counted_in_one_scan():
    """Asking per source means four full scans of 380k rows, and there is no guarantee the four fit inside
    statement_timeout (30s) -- all the more so while three other workers read the same DB."""
    mix = loaded()
    asked: list[str] = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, sql, *_params):
            asked.append(sql)

        def fetchall(self):
            return [("youtube_comment", 288914, 285735), ("commerce_review", 23156, 22889)]

    class Conn:
        def cursor(self):
            return Cursor()

        def commit(self):
            asked.append("commit")

    rows = mix.composition(Conn())
    statements = [sql for sql in asked if sql != "commit"]
    assert len(statements) == 1, statements
    assert "GROUP BY source" in statements[0]
    assert "commit" in asked, "38만 청크 색인을 여는 것이 뒤따른다 -- 트랜잭션을 열어 둔 채로 나가지 않는다"
    assert rows[0].source == "youtube_comment" and rows[0].chunks == 288914 and rows[0].docs == 285735
    assert round(rows[0].chunk_share, 4) == round(288914 / (288914 + 23156), 4)


# Nine dominant-source chunks say the same word densely, and one minority-source chunk says the same word
# once at length -- last in the global ranking. It is only the shape §Per-source allocation measured, cut
# down to ten rows.
CHUNKS = [(f"youtube_comment:c{i}#0", "youtube_comment", "백탁 백탁 백탁") for i in range(9)] + [
    ("commerce_review:r0#0", "commerce_review", "백탁 " + "끈적임 " * 60)
]


@pytest.fixture
def conn(needs_runtime_url: str):
    """The role the pipeline runs as. The place `sources` narrows is SQL, so a real table is needed to
    measure it.

    The same way as `test_pipeline.conn`, but no source schema is built -- what is measured here is not
    chunking but a search over a table that already holds chunks."""
    from sqlalchemy.engine import make_url

    from tests.retrieval.conftest import install_topics

    parsed = make_url(needs_runtime_url)
    connection = psycopg.connect(
        host=parsed.host,
        port=parsed.port,
        user=parsed.username,
        password=parsed.password,
        dbname=parsed.database,
        options=parsed.query["options"],  # pyright: ignore[reportArgumentType]
    )
    install_topics(connection)  # the index does not stand without an active topic dictionary (#8)
    with connection.cursor() as cur:
        cur.executemany(
            "INSERT INTO retrieval_chunk (chunk_id, doc_id, source, ordinal, text, text_md5) "
            "VALUES (%s, %s, %s, 0, %s, md5(%s))",
            [(cid, cid.split("#")[0], source, text, text) for cid, source, text in CHUNKS],
        )
    connection.commit()
    try:
        yield connection
    finally:
        connection.close()


@pytest.mark.postgres
def test_sources_narrows_the_pool(conn):
    """It burns down to the SQL to see whether `--source` really narrows -- the narrowing is in the WHERE of
    `load_index`, and an injected index skips that place wholesale and leaves only the name."""
    pytest.importorskip("kiwipiepy")
    from analysis.retrieval import pipeline

    hits = pipeline.ranked_chunks(conn, "백탁", top=10, sources=("commerce_review",), cache_dir=None)
    assert [chunk_id for chunk_id, _ in hits] == ["commerce_review:r0#0"]


@pytest.mark.postgres
def test_the_narrowed_pool_is_still_ranked_globally_not_by_share(conn):
    """Catches the contract's property in code -- the day an allocation is added this line goes red, and then
    §Per-source allocation is fixed with it.

    Without the narrowing the minority source gets no slot in the top k. Had there been a share, some of the
    ten slots would have to be that source's; here relevance alone decides the slots."""
    pytest.importorskip("kiwipiepy")
    from analysis.retrieval import pipeline

    hits = pipeline.ranked_chunks(conn, "백탁", top=5, cache_dir=None)
    assert [chunk_id for chunk_id, _ in hits] == [f"youtube_comment:c{i}#0" for i in range(5)]
    assert all(not chunk_id.startswith("commerce_review:") for chunk_id, _ in hits)
    # Those minority chunks were not dropped from the candidates but **pushed out** -- widen k and they come
    # out.
    deeper = [chunk_id for chunk_id, _ in pipeline.ranked_chunks(conn, "백탁", top=10, cache_dir=None)]
    assert deeper[-1] == "commerce_review:r0#0"


def test_the_contract_carries_the_verdict_and_the_numbers_it_was_measured_with():
    """Copy only the numbers and let the constant drift, and the next person puts a value measured by another
    criterion into this table."""
    mix = loaded()
    body = section()
    assert f"**{mix.NO_SKEW}**" in body
    for kind in (mix.NO_SKEW, mix.NOT_BURIED, mix.DOMINATED):
        assert f"| {kind} |" in body, kind
    assert f"{mix.BURIED_RANK}위" in body
    assert "결과를 보고 기준을 만들지 않는다" in body
    # The composition and the top-k share have to be there **together** for the judgement to be read. With
    # only one, the other is imagined.
    assert "75.64%" in body and "71.11%" in body
    assert "381,950" in body
    # Without ydc's numbers, what this section was arguing against disappears.
    assert "293위" in body and "92%" in body
    # The place where the minority source is in more of them is the heart of this judgement.
    assert "commerce_review" in body and "21.03%" in body


def test_the_decision_says_what_would_have_been_built_and_why_it_was_not():
    """With only the numbers left and the decision gone, the next person just puts RRF in."""
    body = section()
    assert "더하지 않는다" in body
    assert "RRF" in body
    assert "§Retrieval measurements" in body, "measured over the same query list, so it joins up with that"


def test_the_search_section_says_the_allocation_is_absent_on_purpose():
    """Absent from the front of the contract, a person using `--source` never meets this decision."""
    body = ENTRYPOINTS.read_text(encoding="utf-8")
    start = body.index("## Search (")
    search = body[start : body.index("\n## ", start)]
    assert "it does not give a per-source share" in search
    assert "§Per-source allocation" in search
