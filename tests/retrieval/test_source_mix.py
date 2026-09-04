"""소스별 분배를 `ranked_chunks` 에 **더하지 않는다**는 결정을 지키는 자리 (포크 #54).

ydc `rag/engine.py` 는 소스마다 따로 뽑아 합친다 -- 색인의 92%가 짧은 유튜브 댓글이라 전역 상위 k 가
`mfds` 를 293위로, `ingredient` 를 300위 밖으로 밀어냈기 때문이다. 그 조건이 우리에게도 있는지를 잰
것이 이 이슈이고, 답은 **없다** 였다. 지킬 것이 셋이다.

**① 판정 기준은 재기 전에 정해졌다.** 세 갈래(`쏠리지 않는다`·`밀리지 않는다`·`지배한다`)와 두 상수
(`K`·`BURIED_RANK`)가 숫자를 보기 전에 고정됐다. 결과를 보고 기준을 만드는 것이 이 측정이 막으려는
일이라, 기준이 조용히 움직이면 판정도 조용히 움직인다 (`test_vector_floor` 와 같은 자리).

**② 분배가 없다는 것은 결정이지 미구현이다가 아니다.** `ranked_chunks` 는 `sources` 로 후보를 좁힐 뿐
남은 것 중 전역 상위 k 를 낸다. 그 성질이 바뀌는 날 계약 §소스별 분배 가 함께 바뀌어야 하므로 여기서 잡는다.

**③ 그 결정이 인용한 수가 아직 참인가.** 재는 길은 `tool/measure-source-mix` 이고, 운영 DB 와 38만 청크
색인을 열어야 해서 이 스위트가 부르지 않는다(§검색 실측 여섯 줄과 같은 자리다). 여기서 붙드는 것은
**표의 모양과 상수**이고, 수 자체의 거처는 계약이다.
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
    """확장자가 없어 평범한 import 로는 안 들어온다 (`test_vector_floor.loaded` 와 같은 길)."""
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
    # 상위 k 의 지배 소스 점유율이 색인 구성비에 못 미치면 ydc 의 조건 자체가 없다 -- 우리 실측이 이 모양이다.
    assert mix.verdict(0.7564, 0.7051, [19, 32, 4]) == mix.NO_SKEW
    # 쏠려도 소수 소스가 k 근처에 있으면 분배가 사는 손해를 정당화하지 못한다.
    assert mix.verdict(0.7564, 0.90, [19, 32, 4]) == mix.NOT_BURIED
    # 쏠리고 소수 소스가 자리를 못 잡으면 ydc 와 같은 조건이다.
    assert mix.verdict(0.7564, 0.90, [293, 300, 431]) == mix.DOMINATED
    # 후보를 가진 소수 소스가 없으면 밀렸다고도 안 밀렸다고도 말할 수 없다.
    assert mix.verdict(0.7564, 0.90, []) == mix.UNMEASURABLE


def test_the_boundaries_are_the_ones_the_criteria_named():
    """부등호를 아무도 안 지키면 `<` 가 `<=` 로 바뀌어도 스위트가 초록이다."""
    mix = loaded()
    # 구성비와 **같은** 점유율은 "구성비를 그대로 따라간다"이므로 쏠림이다.
    assert mix.verdict(0.7564, 0.7564, [4]) != mix.NO_SKEW
    # 중앙값이 문턱과 같으면 밀린 것이다 -- 문턱은 "이만큼부터 밀림"이다.
    assert mix.verdict(0.7564, 0.90, [mix.BURIED_RANK]) == mix.DOMINATED
    assert mix.verdict(0.7564, 0.90, [mix.BURIED_RANK - 1]) == mix.NOT_BURIED


def test_the_median_carries_the_verdict_not_the_worst_query():
    """한 질의의 777위로 판정하면 어떤 코퍼스에서도 지배가 나온다 -- 꼬리는 언제나 길다."""
    mix = loaded()
    assert mix.verdict(0.7564, 0.90, [1, 2, 777]) == mix.NOT_BURIED


def test_the_composition_is_counted_in_one_scan():
    """소스마다 물으면 38만 행 전량 훑기가 네 번이고, 그 넷이 statement_timeout(30초) 안에 든다는
    보장이 없다 -- 다른 워커 셋이 같은 DB 를 읽는 동안에는 더욱 그렇다."""
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


# 지배 소스 아홉이 같은 낱말을 밀도 높게 말하고, 소수 소스 하나는 같은 낱말을 길게 한 번 말한다 --
# 전역 순위에서는 마지막이다. §소스별 분배 가 잰 그 모양을 열 행으로 줄인 것뿐이다.
CHUNKS = [(f"youtube_comment:c{i}#0", "youtube_comment", "백탁 백탁 백탁") for i in range(9)] + [
    ("commerce_review:r0#0", "commerce_review", "백탁 " + "끈적임 " * 60)
]


@pytest.fixture
def conn(needs_runtime_url: str):
    """파이프라인이 도는 롤. `sources` 로 좁히는 자리가 SQL 이라 진짜 표가 있어야 재진다.

    `test_pipeline.conn` 과 같은 길이되 원천 스키마는 세우지 않는다 -- 여기서 재는 것은 청킹이 아니라
    이미 청크가 있는 표 위의 검색이다."""
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
    install_topics(connection)  # 색인은 활성 주제 사전 없이는 서지 않는다 (#8)
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
    """`--source` 가 실제로 좁히는지를 SQL 까지 태워 본다 -- 좁힘은 `load_index` 의 WHERE 에 있고,
    색인을 주입하면 그 자리가 통째로 건너뛰어져 이름만 남는다."""
    pytest.importorskip("kiwipiepy")
    from analysis.retrieval import pipeline

    hits = pipeline.ranked_chunks(conn, "백탁", top=10, sources=("commerce_review",), cache_dir=None)
    assert [chunk_id for chunk_id, _ in hits] == ["commerce_review:r0#0"]


@pytest.mark.postgres
def test_the_narrowed_pool_is_still_ranked_globally_not_by_share(conn):
    """계약의 성질을 코드에서 잡는다 -- 분배를 넣는 날 이 줄이 빨개지고, 그때 §소스별 분배 를 함께 고친다.

    좁히지 않으면 소수 소스는 상위 k 에 자리를 못 받는다. 몫이 있었다면 열 자리 중 얼마는 그 소스의
    것이어야 하는데, 여기서는 관련도만 자리를 정한다."""
    pytest.importorskip("kiwipiepy")
    from analysis.retrieval import pipeline

    hits = pipeline.ranked_chunks(conn, "백탁", top=5, cache_dir=None)
    assert [chunk_id for chunk_id, _ in hits] == [f"youtube_comment:c{i}#0" for i in range(5)]
    assert all(not chunk_id.startswith("commerce_review:") for chunk_id, _ in hits)
    # 그 소수 청크는 후보에서 빠진 것이 아니라 **밀린** 것이다 -- k 를 넓히면 나온다.
    deeper = [chunk_id for chunk_id, _ in pipeline.ranked_chunks(conn, "백탁", top=10, cache_dir=None)]
    assert deeper[-1] == "commerce_review:r0#0"


def test_the_contract_carries_the_verdict_and_the_numbers_it_was_measured_with():
    """수만 옮겨 적고 상수가 갈리면 다음 사람이 다른 기준으로 잰 값을 이 표에 넣는다."""
    mix = loaded()
    body = section()
    assert f"**{mix.NO_SKEW}**" in body
    for kind in (mix.NO_SKEW, mix.NOT_BURIED, mix.DOMINATED):
        assert f"| {kind} |" in body, kind
    assert f"{mix.BURIED_RANK}위" in body
    assert "결과를 보고 기준을 만들지 않는다" in body
    # 구성비와 상위 k 점유율이 **함께** 있어야 판정이 읽힌다. 하나만 있으면 다른 쪽을 상상하게 된다.
    assert "75.64%" in body and "71.11%" in body
    assert "381,950" in body
    # ydc 의 수가 없으면 이 절이 무엇을 반박했는지가 사라진다.
    assert "293위" in body and "92%" in body
    # 소수 소스가 오히려 더 많이 든 자리가 이 판정의 핵심이다.
    assert "commerce_review" in body and "21.03%" in body


def test_the_decision_says_what_would_have_been_built_and_why_it_was_not():
    """수만 남고 결정이 사라지면 다음 사람이 RRF 를 그냥 넣는다."""
    body = section()
    assert "더하지 않는다" in body
    assert "RRF" in body
    assert "§검색 실측" in body, "같은 질의 목록으로 잰 것이라 그쪽과 이어져 있어야 한다"


def test_the_search_section_says_the_allocation_is_absent_on_purpose():
    """계약의 입구 쪽에 없으면 `--source` 를 쓰는 사람은 이 결정을 영영 안 만난다."""
    body = ENTRYPOINTS.read_text(encoding="utf-8")
    start = body.index("## 검색 (")
    search = body[start : body.index("\n## ", start)]
    assert "소스별 몫을 주지 않는다" in search
    assert "§소스별 분배" in search
