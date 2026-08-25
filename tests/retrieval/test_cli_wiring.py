"""`cosmai retrieval <하위명령>` 이 실제로 일하는 함수까지 닿는지.

두 번 다 여기서 죽었다(2026-08-25, 둘 다 인코딩 시작 0.3초 만에).
  1. 공통 디스패치가 `args.source` 를 읽는데 `embed` 에는 그 옵션이 없었다.
  2. 호출부만 고치고 `_run_retrieval_embed` 의 시그니처는 그대로여서 인자 수가 안 맞았고,
     그 함수 본문은 아직 pgvector 시절이라 이제 없는 이름을 참조하고 있었다.

연결에서 멈추는 테스트로는 2번을 못 잡는다. 그래서 연결을 가짜로 통과시키고 **일하는 함수가
불렸는지**까지 본다 -- DB 도 모델도 부르지 않는다."""

from __future__ import annotations

import pytest

from cosmai.cli import RETRIEVAL_ENGINES, RETRIEVAL_SOURCES, build_parser, main


class FakeConn:
    """`with conn:` 만 되면 된다. 아무 쿼리도 받지 않는다."""

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


@pytest.fixture
def refuse_connection(monkeypatch):
    """소켓을 열지 않고 연결만 거절한다. tests/conftest.py 의 오프라인 가드는 RuntimeError 를
    던지는데, 그것은 CLI 가 잡는 예외가 아니라 테스트 하네스의 신호다."""
    import psycopg

    from cosmai import cli

    def refused(_url):
        raise psycopg.OperationalError("연결 거절 (테스트)")

    monkeypatch.setattr(cli, "_connect", refused)


@pytest.fixture
def worked(monkeypatch):
    """연결을 통과시키고 일하는 함수를 가짜로 바꾼다. 무엇이 어떤 인자로 불렸는지 남긴다."""
    from analysis.retrieval import embed, pipeline
    from analysis.retrieval import eval as retrieval_eval
    from cosmai import cli

    calls: dict[str, dict] = {}
    monkeypatch.setattr(cli, "_connect", lambda _url: FakeConn())

    def chunk(_conn, **kw):
        calls["chunk"] = kw
        return pipeline.ChunkOutcome(1, 1, 1, [])

    def search(_conn, query, **kw):
        calls["search"] = {"query": query, **kw}
        return [("d1#0", 1.0, "본문")]

    def score(_conn, mode, **kw):
        calls["eval"] = {"mode": mode, **kw}
        return [retrieval_eval.Row(mode, kw.get("engine", "bm25"), "t", "q", 1, 1, 1.0, 1.0, True)]

    def encode(_conn, **kw):
        calls["embed"] = kw
        return embed.EmbedOutcome("m", "r", 1, kw["out"])

    monkeypatch.setattr(pipeline, "run", chunk)
    monkeypatch.setattr(pipeline, "search", search)
    monkeypatch.setattr(retrieval_eval, "run", score)
    monkeypatch.setattr(embed, "run", encode)
    return calls


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
def test_a_refused_connection_is_blocked_not_failed(argv, refuse_connection, capsys):
    # exit 2 는 "아직 아무것도 시작하지 못한 거절"이다. 인자 해석이 연결보다 앞이라 여기까지 온다.
    assert main(argv) == 2
    assert "연결 거절" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("argv", "key"),
    [
        (["retrieval", "chunk"], "chunk"),
        (["retrieval", "search", "--query", "백탁"], "search"),
        (["retrieval", "eval", "--mode", "literal"], "eval"),
        (["retrieval", "embed"], "embed"),
    ],
    ids=lambda a: a if isinstance(a, str) else a[1],
)
def test_every_subcommand_calls_its_worker(argv, key, worked, capsys):
    assert main(argv) == 0
    assert key in worked, f"{key} 가 불리지 않았다: {sorted(worked)}"
    assert capsys.readouterr().out.strip()


def test_the_vector_store_path_reaches_the_worker(worked, tmp_path):
    out = tmp_path / "e5base"
    assert main(["retrieval", "embed", "--vectors", str(out)]) == 0
    assert worked["embed"]["out"] == out


def test_the_engine_reaches_search_and_eval(worked):
    main(["retrieval", "search", "--query", "백탁", "--engine", "hybrid"])
    main(["retrieval", "eval", "--mode", "heldout", "--engine", "vector"])
    assert worked["search"]["engine"] == "hybrid"
    assert worked["eval"]["engine"] == "vector"


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
