"""`cosmai retrieval <하위명령>` 이 실제로 디스패치까지 가는지. 하위명령마다 있는 옵션이 다른데
공통 코드가 없는 것을 읽으면 AttributeError 로 죽는다 -- `embed` 에 `--source` 가 없어서
실제로 그렇게 죽었다(2026-08-25, 인코딩 시작 0.3초 만에).

DB 는 붙지 않는다. 연결을 거절로 갈아 끼우고 exit 2 를 확인한다 -- 인자 해석이 연결보다 앞에
있으므로, 옵션 하나가 빠진 하위명령은 여기까지 오지 못한다."""

from __future__ import annotations

import pytest

from cosmai.cli import RETRIEVAL_ENGINES, RETRIEVAL_SOURCES, build_parser, main


@pytest.fixture
def refuse_connection(monkeypatch):
    """소켓을 열지 않고 연결만 거절한다. tests/conftest.py 의 오프라인 가드는 RuntimeError 를
    던지는데, 그것은 CLI 가 잡는 예외가 아니라 테스트 하네스의 신호다."""
    import psycopg

    from cosmai import cli

    def refused(_url):
        raise psycopg.OperationalError("연결 거절 (테스트)")

    monkeypatch.setattr(cli, "_connect", refused)


@pytest.mark.parametrize(
    "argv",
    [
        ["retrieval", "chunk"],
        ["retrieval", "search", "--query", "백탁"],
        ["retrieval", "eval", "--mode", "literal"],
        ["retrieval", "embed"],
    ],
    ids=lambda a: a[1],
)
def test_every_subcommand_reaches_the_connection(argv, refuse_connection, capsys):
    assert main(argv) == 2
    # exit 2 는 "아직 아무것도 시작하지 못한 거절"이다. 파서가 아니라 연결에서 멈춰야 한다.
    assert "연결 거절" in capsys.readouterr().out


def test_the_vector_store_flag_is_spelled_the_same_everywhere():
    # `--out` 이 eval 에서는 CSV, embed 에서는 벡터 경로였다. 같은 이름이 다른 뜻이면 헷갈린다.
    parser = build_parser()
    for action in ("search", "eval", "embed"):
        argv = ["retrieval", action, "--vectors", "x"]
        if action == "search":
            argv += ["--query", "백탁"]
        if action == "eval":
            argv += ["--mode", "literal"]
        assert parser.parse_args(argv).vectors == "x"


def test_the_engine_and_source_vocabularies_are_shared():
    parser = build_parser()
    args = parser.parse_args(
        ["retrieval", "search", "--query", "백탁", "--engine", "hybrid", "--source", "commerce_review"]
    )
    assert args.engine in RETRIEVAL_ENGINES
    assert args.source == ["commerce_review"]
    assert set(args.source) <= set(RETRIEVAL_SOURCES)
