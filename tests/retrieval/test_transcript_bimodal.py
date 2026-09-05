"""The measurement behind fork #65 -- why `youtube_transcript` is bimodal in the global top 10.

`#54` left a shape rather than a cause: the source is 16.75% of the index, takes 5.47% of the global
top 10, and splits 47 buried / 11 in the top 10. `tool/measure-transcript-bimodal` measures the two
candidate causes -- chunk length and term competition -- and prices a `bm25.B` move on the
§Retrieval measurements baseline. What this file holds is the **method**, not the numbers: the
numbers need the production DB and a 380k-chunk index, so their home is the contract and the issue
(the same place `tests/retrieval/test_source_mix.py` leaves them).

Three things have to stay true or the measurement stops measuring what it claims.

**(1) The sweep's knob is the scorer's knob.** `B` is a module constant read inside `Index.search`,
so a sweep that set anything else would report a curve that no search ever runs.

**(2) The first appearance is taken over the full ranking.** Half of this issue is where beyond the
top 10 a source first shows up; a measurement that only looked at the top k would report "absent"
for rank 11 and rank 431 alike.

**(3) The eval part scores the way `eval.run` scores.** It exists to price a `B` change against
`§Retrieval measurements`, and a different candidate depth or a different fold to documents would
produce a number that cannot be compared with that table.
"""

from __future__ import annotations

from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from types import ModuleType

import pytest

from analysis.retrieval import bm25
from analysis.retrieval import eval as retrieval_eval

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tool" / "measure-transcript-bimodal"

# Latin text keeps these units off Kiwi: the regex branch of `tokenize` gives one token per word, so
# the lengths below are the lengths the assertions talk about.
SHORT = "haze"
LONG = "haze " + "filler " * 80


def loaded() -> ModuleType:
    """The tool has no extension, so a plain import does not reach it (`test_source_mix.loaded` and
    `test_vector_floor.loaded` take the same path)."""
    spec = spec_from_loader(
        "measure_transcript_bimodal", SourceFileLoader("measure_transcript_bimodal", str(TOOL))
    )
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def two_sources() -> tuple[bm25.Index, dict[str, str]]:
    """One long transcript chunk and one short comment chunk, both holding the query term once, plus
    filler documents so the term's idf is above zero. Same term, same tf, different length: the only
    thing that can separate their scores is the length normalisation."""
    ids = ["youtube_transcript:a#0", "youtube_comment:z#0"] + [f"youtube_comment:f{i}#0" for i in range(8)]
    texts = [LONG, SHORT] + ["filler"] * 8
    origin = {ids[0]: "youtube_transcript"} | {i: "youtube_comment" for i in ids[1:]}
    return bm25.Index(ids, texts), origin


def test_the_sweep_turns_the_same_knob_the_scorer_reads():
    """`length_norm` has to reach `Index.search`, and it has to put `B` back -- the sweep runs six
    passes in one process and a leaked value would silently rescore the rest of them."""
    tool = loaded()
    index, _origin = two_sources()
    before = bm25.B

    with tool.length_norm(0.75):
        on = dict(index.search("haze", k=2))
    with tool.length_norm(0.0):
        off = dict(index.search("haze", k=2))
    # With normalisation the 81-token chunk scores below the 1-token one; with it off they are the
    # same score, because tf, idf and K1 are all equal here.
    assert on["youtube_comment:z#0"] > on["youtube_transcript:a#0"]
    assert off["youtube_comment:z#0"] == off["youtube_transcript:a#0"]
    assert bm25.B == before

    with pytest.raises(RuntimeError):
        with tool.length_norm(0.0):
            raise RuntimeError("a pass that blows up still has to put B back")
    assert bm25.B == before


def test_the_first_appearance_is_taken_over_the_full_ranking():
    """A source at rank 11 and a source at rank 431 are both "not in the top 10", and this issue is
    about the difference between them."""
    tool = loaded()
    ids = [f"youtube_comment:c{i}#0" for i in range(12)] + ["youtube_transcript:t#0"]
    texts = ["haze"] * 12 + [LONG]
    origin = {
        i: ("youtube_transcript" if i.startswith("youtube_transcript") else "youtube_comment") for i in ids
    }
    index = bm25.Index(ids, texts)

    measured = tool.one_pass(index, origin, [("topic", "haze")], k=10)
    transcript = measured["first_rank"]["youtube_transcript"]
    assert transcript["median"] == 13, "the rank it actually holds, not merely absent"
    assert transcript["beyond_k"] == 1 and transcript["within_k"] == 0
    assert measured["top_k"]["youtube_comment"]["n"] == 10
    assert "youtube_transcript" not in measured["top_k"], "it is not in the top 10, only in the ranking"


def test_the_df_split_counts_only_the_terms_the_scorer_scores():
    """A term with df 0 has idf 0 and moves no ranking, so counting it would put competition where
    the scorer sees none."""
    tool = loaded()
    index, origin = two_sources()
    at = tool.source_at(index, origin)

    split = tool.source_df(index, at, "haze nosuchterm")
    assert set(split) == {"youtube_transcript", "youtube_comment"}
    assert split["youtube_transcript"]["df"] == 1 and split["youtube_comment"]["df"] == 1
    assert split["youtube_transcript"]["median_len"] == 81, "the long chunk is the one being penalised"
    assert tool.source_df(index, at, "nosuchterm") == {}


def test_the_eval_part_scores_the_way_eval_run_does(monkeypatch):
    """It prices a `B` move against `§Retrieval measurements`, so it has to ask for the same
    candidate depth (`k * 4`) and fold to documents the same way -- otherwise the before/after is not
    on that table's axis."""
    pytest.importorskip("kiwipiepy")
    tool = loaded()
    from tests.retrieval.conftest import csv_topics

    dictionary = csv_topics()
    asked: list[int] = []

    class StubIndex:
        n = 1
        doc_ids: list[str] = []
        postings: dict[str, list[tuple[int, int]]] = {}

        def search(self, query: str, k: int | None = 10, skip: set[str] | None = None):
            asked.append(k or 0)
            # Four chunks per document, which is why the fold to documents is what k counts.
            return [(f"youtube_transcript:d{i // 4}#{i % 4}", 1.0) for i in range(k or 0)]

    gold = {entry["topic"]: {f"youtube_transcript:d{i}" for i in range(10)} for entry in dictionary.entries}
    monkeypatch.setattr(retrieval_eval, "gold_from_chunks", lambda *_a, **_kw: gold)

    measured = tool.eval_sweep(None, StubIndex(), {}, dictionary, (0.75,))
    assert asked and set(asked) == {retrieval_eval.K * 4}, "the same candidate depth as eval.run"
    literal = measured["0.75"]["literal"]
    assert literal["queries"] == len(retrieval_eval.queries("literal", dictionary))
    # Ten documents folded out of forty chunks, all of them gold: a perfect score is the arithmetic
    # proof that the fold happened -- without it the ten ranked ids would be four chunks of one doc.
    assert literal["p_at_k"] == 1.0 and literal["hit"] == 1.0
    assert literal["transcript_docs_in_top_k"] == 10 * literal["queries"]


def test_the_sweep_always_measures_the_value_the_scorer_runs():
    """The answer #65 reached is "B does not move", and that answer is only readable against the
    column the running code sits in. A sweep without it would print a curve with no baseline."""
    tool = loaded()
    assert bm25.B in tool.B_SWEEP
    # Measured on 381,950 chunks (2026-09-04): at 0.75 the transcript takes 5.47% of the global top
    # 10 and at 0.50 it takes 22.22%, while literal P@10 moves .868 -> .871 and heldout bm25 stays
    # .000 at every B. The only score that moves is the one whose gold is denser in long chunks.
    assert bm25.B == 0.75


def test_the_gold_density_is_reported_per_chunk_and_per_character():
    """Per chunk, a 480-character transcript window looks far more likely to be gold than a
    36-character comment; per 1k characters that advantage is gone. Leaving `B` alone rests on that
    difference being visible, so the tool has to keep both axes -- with only the per-chunk number,
    a lower `B` reads as better retrieval."""
    tool = loaded()
    from tests.retrieval.conftest import csv_topics

    dictionary = csv_topics()
    alias = dictionary.entries[0]["ko"][0]
    long_text = alias + (" filler" * 100)

    class Cursor:
        def __init__(self, text: str):
            self.text = text

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, _sql, params):
            self.rows = [(self.text,)] * 2

        def fetchall(self):
            return self.rows

    class Conn:
        def __init__(self, text: str):
            self.text = text

        def cursor(self):
            return Cursor(self.text)

        def commit(self):
            return None

    short = tool.gold_density(Conn(alias), dictionary, ["youtube_comment"], sample=2)["youtube_comment"]
    long = tool.gold_density(Conn(long_text), dictionary, ["youtube_transcript"], sample=2)[
        "youtube_transcript"
    ]
    assert short["label_rate"] == long["label_rate"] == 1.0
    assert short["labels_per_chunk"] == long["labels_per_chunk"]
    assert short["labels_per_1k_chars"] > long["labels_per_1k_chars"]
