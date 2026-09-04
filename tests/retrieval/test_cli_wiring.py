"""Whether `cosmai retrieval <subcommand>` really reaches the function that does the work.

It died here twice (2026-08-25, both 0.3 seconds into the encoding).
  1. The shared dispatch read `args.source`, which `embed` did not have as an option.
  2. Only the call site was fixed and the signature of `_run_retrieval_embed` stayed, so the argument count
     did not match, and that function's body was still from the pgvector era and referred to names that no
     longer exist.

A test that stops at the connection cannot catch the second. So the connection is passed through a fake and
it checks **whether the function that does the work was called** -- neither the DB nor the model is
called."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from cosmai.cli import RETRIEVAL_ENGINES, RETRIEVAL_SOURCES, build_parser, main


class FakeConn:
    """`with conn:` is all that is needed. It takes no query at all."""

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


@pytest.fixture
def refuse_connection(monkeypatch):
    """Refuses the connection without opening a socket. The offline guard of tests/conftest.py raises
    RuntimeError, which is a signal of the test harness rather than an exception the CLI catches."""
    import psycopg

    from cosmai import cli

    def refused(_url):
        raise psycopg.OperationalError("연결 거절 (테스트)")

    monkeypatch.setattr(cli, "_connect", refused)


@pytest.fixture
def worked(monkeypatch):
    """Lets the connection through and swaps the working function for a fake. It records what was called with
    which arguments."""
    from analysis.retrieval import embed, pipeline, terms
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

    def look(_conn, **kw):
        calls["terms"] = kw
        return SimpleNamespace(documents={"topical": 1})

    monkeypatch.setattr(pipeline, "run", chunk)
    monkeypatch.setattr(pipeline, "search", search)
    monkeypatch.setattr(retrieval_eval, "run", score)
    monkeypatch.setattr(embed, "run", encode)
    monkeypatch.setattr(terms, "scan", look)
    monkeypatch.setattr(terms, "render", lambda _scanned, **_kw: "표")
    return calls


@pytest.mark.parametrize(
    "argv",
    [
        ["retrieval", "chunk"],
        ["retrieval", "search", "--query", "백탁"],
        ["retrieval", "eval", "--mode", "literal"],
        ["retrieval", "embed"],
        ["retrieval", "terms"],
    ],
    ids=lambda a: a[1],
)
def test_a_refused_connection_is_blocked_not_failed(argv, refuse_connection, capsys):
    # exit 2 is "a refusal before anything could start". Argument parsing comes before the connection, so
    # it gets this far.
    assert main(argv) == 2
    assert "연결 거절" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("argv", "key"),
    [
        (["retrieval", "chunk"], "chunk"),
        (["retrieval", "search", "--query", "백탁"], "search"),
        (["retrieval", "eval", "--mode", "literal"], "eval"),
        (["retrieval", "embed"], "embed"),
        (["retrieval", "terms"], "terms"),
    ],
    ids=lambda a: a if isinstance(a, str) else a[1],
)
def test_every_subcommand_calls_its_worker(argv, key, worked, capsys):
    assert main(argv) == 0
    assert key in worked, f"{key} 가 불리지 않았다: {sorted(worked)}"
    assert capsys.readouterr().out.strip()


def test_an_eval_that_scores_no_query_is_partial(worked, monkeypatch):
    """청크가 비었거나 사전이 안 얹히면 질의가 하나도 채점되지 않는다 -- 조용한 0 은 녹색으로
    읽히므로 partial 이다(계약 §검색 종료 코드, #17 S6)."""
    from analysis.retrieval import eval as retrieval_eval

    monkeypatch.setattr(retrieval_eval, "run", lambda *_a, **_kw: [])
    assert main(["retrieval", "eval", "--mode", "literal"]) == 1


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
    # `--out` was the CSV for eval and the vector path for embed. The same name meaning two things confuses.
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


def test_a_scan_that_saw_no_document_is_partial(worked, monkeypatch):
    """An empty table reads as "the dictionary caught everything" -- green while it means the chunks are
    empty is worse."""
    from analysis.retrieval import terms

    monkeypatch.setattr(terms, "scan", lambda *_a, **_kw: SimpleNamespace(documents={}))
    assert main(["retrieval", "terms"]) == 1
