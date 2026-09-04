"""The answer layer's promises: no evidence means no call, a document is one citation, and every
real call leaves a row that says which index and which lexicon it stood on.

There is no real call anywhere in this file. The client is a fake that records what it was handed,
which is also how the "text blocks only" rule is tested -- a thinking block first is exactly the
shape that used to raise (ydc 5354ee9).

The corpus text is ASCII on purpose. `bm25.tokenize` sends non-Korean text down the regex branch,
so these tests need neither Kiwi nor a Korean literal, and the repository's language rule
(tool/checks/lang) stays satisfied without a fixture directory.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import psycopg
import pytest
from sqlalchemy.engine import make_url

from analysis.crosscheck import PAPER_HOLD
from analysis.polarity.pricing import UsageLedger
from analysis.retrieval import ask
from tests.retrieval.conftest import install_topics

pytestmark = pytest.mark.postgres

QUERY = "panthenol"
# Long enough for the grounding gate to treat a chunk frequency of 0 as "the corpus never says this
# name" (grounding.ZERO_DF_MINLEN), and absent from the rows below.
ABSENT = "xyzzyplex"
ANSWER = "## Core\nPanthenol is mentioned. [Source: d1]\n\n## Evidence summary\n- one\n\n## Limits\n- chunks"

ROWS = (
    ("d1", 0, "panthenol calms the skin"),
    ("d1", 1, "panthenol again in the second piece of the same document"),
    ("d2", 0, "panthenol shows up in a review too"),
    ("d3", 0, "the texture is sticky and nothing else"),
)


class FakeClient:
    """Records `messages.create(**params)` and answers with the block list a real message has."""

    def __init__(self, blocks=None, usage=None, stop_reason="end_turn"):
        self.calls: list[dict] = []
        self.stop_reason = stop_reason
        self.blocks = blocks if blocks is not None else [SimpleNamespace(type="text", text=ANSWER)]
        self.usage = usage or SimpleNamespace(
            input_tokens=1000, output_tokens=200, cache_read_input_tokens=0, cache_creation_input_tokens=0
        )

    @property
    def messages(self):
        return self

    def create(self, **params):
        self.calls.append(params)
        return SimpleNamespace(content=self.blocks, usage=self.usage, stop_reason=self.stop_reason)


@pytest.fixture
def loaded(needs_schema: str, needs_runtime_url: str):
    """Chunks written as needs_runtime, the role production runs as, with the topic dictionary in
    the same schema -- the fixture shape tests/retrieval/test_eval.py uses."""
    parsed = make_url(needs_runtime_url)
    conn = psycopg.connect(
        host=parsed.host,
        port=parsed.port,
        user=parsed.username,
        password=parsed.password,
        dbname=parsed.database,
        options=parsed.query["options"],  # pyright: ignore[reportArgumentType]
    )
    install_topics(conn)
    with conn.cursor() as cur:
        for doc, ordinal, text in ROWS:
            cur.execute(
                "INSERT INTO retrieval_chunk (chunk_id, doc_id, source, ordinal, text, text_md5) "
                "VALUES (%s, %s, 'youtube_comment', %s, %s, 'x')",
                (f"{doc}#{ordinal}", doc, ordinal, text),
            )
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


def log_rows(conn) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT query, engine, gate_ok, token_df, doc_ids, index_fingerprint, dictionary_stamp, "
            "store_stamp, model, usd, answer_chars FROM retrieval_ask_log ORDER BY id"
        )
        rows = cur.fetchall()
    conn.commit()
    return rows


def ask_it(conn, query=QUERY, **kw):
    return ask.run(conn, query, cache_dir=None, **kw)


# ---------- the prompt, with no database ----------


def test_the_vector_and_prediction_rules_carry_no_percentages():
    """The vector figure is a topic-lexicon v1 record and production runs v3; hit rate and base rate
    belong to the verdict population. Either number inside a chunk-level answer mixes axes (#63)."""
    written = ask.system_prompt()
    assert "%" not in written
    assert "13" not in written and "22" not in written and "47" not in written
    # The principle has to survive the loss of the number, or deleting it deleted the rule.
    assert "frequency of 0" in written and "predict the future" in written


def test_the_papers_rule_follows_paper_hold():
    """The rule is read from the constant, not copied. The day the axis opens the prompt has to
    stop claiming it is shut, and that is one import rather than an edit here."""
    assert ("papers or research trends" in ask.system_prompt()) is PAPER_HOLD
    assert "papers or research trends" not in ask.system_prompt(paper_hold=False)


def test_the_deleted_ydc_rules_are_gone_and_the_kept_ones_are_there():
    written = ask.system_prompt()
    # ydc rules 5 and 7 name sources this repository does not have.
    assert "mfds" not in written.lower() and "temporal_filter" not in written
    # ydc's two sentences that survive: scales do not compare, sources stay apart.
    assert "cannot be compared" in written and "keep the sources apart" in written
    assert list(ask.SECTIONS) == ["## Core", "## Evidence summary", "## Limits"]


def test_the_citation_marker_is_the_document_not_the_chunk():
    assert "[Source: doc_id]" in ask.system_prompt()
    folded = ask.fold([("d1#0", 1.0, "a"), ("d1#1", 0.5, "b")], {"d1#0": "youtube_comment"})
    assert [(e.doc_id, e.text, e.chunks) for e in folded] == [("d1", "a b", 2)]


def test_answer_text_takes_the_text_blocks_only():
    """Sonnet 5 puts a thinking block first; reading `.text` off `content[0]` is how ydc died."""
    message = SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking="not the answer"),
            SimpleNamespace(type="text", text="## Core"),
            SimpleNamespace(type="text", text="## Limits"),
        ]
    )
    assert ask.answer_text(message) == "## Core\n## Limits"


# ---------- the pipeline, on the fixture corpus ----------


def test_no_evidence_calls_nothing_and_says_so(loaded):
    client = FakeClient()
    answer = ask_it(loaded, ABSENT, client=client)
    assert client.calls == []
    assert answer.status == "no_evidence"
    assert answer.text == ask.CANNOT_ANSWER
    assert log_rows(loaded) == []


def test_a_query_the_gate_blocks_never_reaches_the_model(loaded):
    """vector and hybrid are gated; a name the corpus never says gets the refusal by the same path
    as rule 3, and the vector store is never opened at all."""
    client = FakeClient()
    answer = ask_it(loaded, ABSENT, engine="vector", client=client)
    assert client.calls == []
    assert answer.status == "no_evidence"
    assert log_rows(loaded) == []


def test_bm25_is_gated_in_ask_because_the_call_is_paid(loaded):
    """`pipeline.search` keeps #48's rule -- bm25 answers with the words that remain. `ask` does not:
    the call is paid, and rule 6 makes the model refuse a df-0 name anyway, so a bm25 query carrying
    one buys a refusal (#76). It ends where the vector-gated case above ends: no call, no row."""
    client = FakeClient()
    # The shape #48 measured: a name the corpus never says beside a word it does say, which is the
    # only query bm25 still answers -- and so the only one that reaches the model today.
    answer = ask_it(loaded, f"{ABSENT} {QUERY}", engine="bm25", client=client)
    assert client.calls == []
    assert answer.status == "no_evidence"
    assert answer.text == ask.CANNOT_ANSWER
    assert answer.evidence == ()
    assert log_rows(loaded) == []


def test_chunks_of_one_document_become_one_piece_of_evidence(loaded):
    client = FakeClient()
    answer = ask_it(loaded, client=client)
    doc_ids = [item.doc_id for item in answer.evidence]
    assert doc_ids == sorted(set(doc_ids), key=doc_ids.index)
    assert "d1" in doc_ids and doc_ids.count("d1") == 1
    d1 = next(item for item in answer.evidence if item.doc_id == "d1")
    assert d1.chunks == 2
    assert "calms the skin" in d1.text and "second piece" in d1.text
    # The prompt lists that document once, with the concatenated text.
    prompt = client.calls[0]["messages"][0]["content"]
    assert prompt.count("- [d1]") == 1
    assert "calms the skin" in prompt and "second piece" in prompt


def test_a_real_call_leaves_one_row_carrying_the_fingerprint(loaded):
    client = FakeClient()
    answer = ask_it(loaded, client=client)
    assert answer.called and answer.status == "ok"
    (row,) = log_rows(loaded)
    query, engine, gate_ok, token_df, doc_ids, index_fp, dictionary, store, model, usd, chars = row
    assert (query, engine, gate_ok) == (QUERY, "bm25", True)
    assert token_df[QUERY] > 0
    assert doc_ids == [item.doc_id for item in answer.evidence]
    assert index_fp and index_fp in answer.note
    assert "ruleset=" in dictionary and dictionary in answer.note
    assert store is None  # bm25 opens no vector store
    assert (model, chars) == (ask.DEFAULT_MODEL, len(answer.text))
    assert usd > 0


def test_a_budget_that_is_already_gone_blocks_before_the_call(loaded):
    """The hard stop is shared with polarity and it fires before the request, not after: money
    already spent does not come back."""
    client = FakeClient()
    ledger = UsageLedger(loaded, budget=Decimal("0.0001"))
    with pytest.raises(ask.BLOCKING):
        ask_it(loaded, client=client, ledger=ledger)
    assert client.calls == []
    assert log_rows(loaded) == []


def test_a_model_with_no_price_is_refused_before_the_corpus_is_touched(loaded):
    client = FakeClient()
    with pytest.raises(ask.BLOCKING):
        ask_it(loaded, model="claude-not-priced", client=client)
    assert client.calls == []


def test_dry_run_calls_nothing_logs_nothing_and_prints_the_prompt(loaded):
    client = FakeClient()
    answer = ask_it(loaded, dry_run=True, client=client)
    assert client.calls == []
    assert log_rows(loaded) == []
    assert answer.status == "ok" and not answer.called and not answer.cost
    assert "== prompt ==" in answer.text and "== evidence ==" in answer.text
    assert QUERY in answer.text and "d1" in answer.text
    # The ledger is untouched too -- a dry run reserves nothing.
    assert UsageLedger(loaded).spent() == 0


def test_a_response_with_thinking_blocks_answers_with_the_text_blocks(loaded):
    """End to end, not just the helper: the answer that reaches stdout and the length that reaches
    the log both have to come from the text blocks, or the thinking leaks into the artefact."""
    client = FakeClient(
        blocks=[
            SimpleNamespace(type="thinking", thinking="deliberating, not the answer"),
            SimpleNamespace(type="text", text=ANSWER),
        ]
    )
    answer = ask_it(loaded, client=client)
    assert answer.text == ANSWER
    assert "deliberating" not in answer.text
    (row,) = log_rows(loaded)
    assert row[-1] == len(ANSWER)


def test_the_note_names_the_index_the_lexicon_and_the_chunk_count(loaded):
    """Without the fingerprint the answer is a falsehood: the retrieval cron is deferred, so the
    index goes out stale by design (#73 item 5)."""
    answer = ask_it(loaded, client=FakeClient())
    assert answer.note.startswith("note: index=")
    assert f"chunks={len(ROWS):,}" in answer.note
    assert "dictionary=ruleset=" in answer.note
    assert "store=" not in answer.note  # bm25 stands on no vector store


def test_the_call_carries_the_system_prompt_and_the_adaptive_thinking_shape(loaded):
    client = FakeClient()
    ask_it(loaded, client=client)
    (params,) = client.calls
    assert params["model"] == ask.DEFAULT_MODEL
    assert params["max_tokens"] == ask.MAX_TOKENS
    assert params["thinking"] == {"type": "adaptive"}
    assert params["system"] == ask.system_prompt()


def test_the_prompt_shows_the_query_token_frequency(loaded):
    client = FakeClient()
    ask_it(loaded, client=client)
    prompt = client.calls[0]["messages"][0]["content"]
    assert "Query token chunk frequency:" in prompt
    assert f"  {QUERY}: 3" in prompt


def test_a_response_of_thinking_only_is_billed_logged_and_refused(loaded):
    """Adaptive thinking spends the same ceiling as the answer, so a call can come back with no
    text at all. The money moved, so the ledger and the log keep it -- but an empty artefact must
    not leave as a finished answer."""
    client = FakeClient(blocks=[SimpleNamespace(type="thinking", thinking="all budget, no answer")])
    answer = ask_it(loaded, client=client)
    assert len(client.calls) == 1
    assert answer.status == "incomplete" and answer.called
    assert answer.text == ask.NO_ANSWER
    assert "no text block" in answer.reason
    (row,) = log_rows(loaded)
    assert row[-1] == 0  # answer_chars
    assert row[-2] > 0  # usd -- the call was paid for
    assert UsageLedger(loaded).spent() > 0


def test_an_answer_cut_off_at_max_tokens_is_not_passed_off_as_complete(loaded):
    """Half of three sections reads exactly like three sections once stdout is a `.md` file."""
    client = FakeClient(
        blocks=[SimpleNamespace(type="text", text="## Core\nHalf a sentence about")],
        stop_reason="max_tokens",
    )
    answer = ask_it(loaded, client=client)
    assert answer.status == "incomplete"
    assert answer.text == ask.NO_ANSWER
    assert "Half a sentence" not in answer.text
    assert "max_tokens" in answer.reason
    (row,) = log_rows(loaded)
    assert row[-1] == len("## Core\nHalf a sentence about")  # what came back is still recorded


def test_the_ceiling_leaves_room_for_thinking_and_the_answer(loaded):
    """ydc's 1100 was a ceiling for the answer alone; adaptive thinking spends the same budget
    (analysis/polarity/llm.py gives a one-sentence classification 4096)."""
    from analysis.polarity import llm

    assert ask.MAX_TOKENS == llm.MAX_TOKENS == 4096
    client = FakeClient()
    ask_it(loaded, client=client)
    assert client.calls[0]["max_tokens"] == ask.MAX_TOKENS


def test_the_index_is_opened_once_per_ask(loaded, monkeypatch):
    """`ask` reads the index for the frequency table and `search` ranks on one. Two opens is a
    second full tokenisation whenever the cache is cold -- invisible in the output, minutes in time."""
    from analysis.retrieval import pipeline

    opened = {"index": 0}
    real = pipeline.load_index

    def counting(*args, **kwargs):
        opened["index"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(pipeline, "load_index", counting)
    ask_it(loaded, client=FakeClient())
    assert opened == {"index": 1}, opened


@pytest.fixture
def vector_store(loaded, tmp_path):
    """A store over exactly the fixture chunks, so the vector path returns them."""
    import numpy as np

    from analysis.retrieval import vectors

    with loaded.cursor() as cur:
        cur.execute("SELECT chunk_id, source FROM retrieval_chunk ORDER BY chunk_id")
        rows = cur.fetchall()
    loaded.commit()
    matrix = np.zeros((len(rows), vectors.DIM), dtype="float32")
    matrix[:, 0] = 1.0
    out = tmp_path / "e5base"
    manifest = {"model": "m", "l2_normalized": True, "query_prefix": "query: ", "dim": vectors.DIM}
    vectors.save(out, matrix, rows, manifest)
    return out


@pytest.fixture
def fake_encoder(monkeypatch):
    from analysis.retrieval import embed, vectors

    class FakeEncoder:
        def encode(self, texts, **_kw):
            return [[1.0] + [0.0] * (vectors.DIM - 1) for _ in texts]

    monkeypatch.setattr(embed, "load_encoder", lambda *_a, **_kw: FakeEncoder())


def test_the_vector_store_is_opened_once_and_stamps_the_row(loaded, vector_store, fake_encoder, monkeypatch):
    """The store is a 1.2GB matrix in production. `ask` reads its stamp and `search` ranks on it;
    opening it twice doubles that read and prints the coverage warning twice."""
    from analysis.retrieval import vectors

    opened = {"store": 0}
    real = vectors.load
    # Read before the counter goes on, so the expected value costs no counted open.
    expected = real(vector_store).stamp

    def counting(path=vectors.DEFAULT_STORE):
        opened["store"] += 1
        return real(path)

    monkeypatch.setattr(vectors, "load", counting)
    answer = ask_it(loaded, engine="vector", store=vector_store, client=FakeClient())
    assert opened == {"store": 1}, opened
    assert answer.status == "ok"
    assert f"store={expected}" in answer.note
    (row,) = log_rows(loaded)
    assert row[7] == expected


def test_the_coverage_warning_is_printed_once(loaded, vector_store, fake_encoder, capsys, monkeypatch):
    """`ranked_chunks` prints it when it opens the store itself; `ask` folds it into the version
    note. Handing the store down is what keeps the person from reading the same warning twice."""
    from analysis.retrieval import pipeline

    monkeypatch.setattr(pipeline, "coverage_note", lambda *_a, **_kw: "warning: drifted (test)")
    answer = ask_it(loaded, engine="vector", store=vector_store, client=FakeClient())
    assert "warning: drifted (test)" in answer.note
    assert capsys.readouterr().err.count("warning: drifted (test)") == 0


def test_the_version_axes_are_read_before_the_evidence_is_ranked(loaded, monkeypatch):
    """An activate landing mid-run must not stamp the row with a lexicon the evidence never stood
    on -- the property #68 pinned for eval rows."""
    from analysis.retrieval import pipeline, topics

    seen: list[str] = []
    real_active, real_search = topics.use_active, pipeline.search

    monkeypatch.setattr(topics, "use_active", lambda conn: (seen.append("dictionary"), real_active(conn))[1])
    monkeypatch.setattr(
        pipeline,
        "search",
        lambda *a, **kw: (seen.append("search"), real_search(*a, **kw))[1],
    )
    ask_it(loaded, client=FakeClient())
    assert "search" in seen
    assert seen.index("search") > 0
    assert "dictionary" not in seen[seen.index("search") :]


# ---------- the CLI ----------


def test_the_cli_default_model_is_the_module_default():
    """Two spellings of one default drift apart silently; the help text is the one people read."""
    from cosmai.cli import RETRIEVAL_ASK_MODEL

    assert RETRIEVAL_ASK_MODEL == ask.DEFAULT_MODEL


def test_the_help_lists_every_argument():
    from cosmai.cli import build_parser

    args = build_parser().parse_args(
        ["retrieval", "ask", "--query", "q", "--engine", "hybrid", "--top", "3",
         "--model", "claude-haiku-4-5", "--dry-run", "--vectors", "v", "--source", "commerce_review"]
    )  # fmt: skip
    assert (args.action, args.query, args.engine, args.top) == ("ask", "q", "hybrid", 3)
    assert (args.model, args.dry_run, args.vectors, args.source) == (
        "claude-haiku-4-5",
        True,
        "v",
        ["commerce_review"],
    )
