"""The file vector store and RRF. The model is not called -- what is measured here is "do the three files
stay as one set · does it stop when they are out of step · does it come out in cosine order". A vector out of
step shows up as a wrong ranking rather than an error, so unless the reading side pins it down nobody
notices."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from analysis.retrieval import vectors

STAMP_TOOL = Path(__file__).resolve().parents[2] / "tool" / "show-vector-stamp"


def _unit(index: int) -> list[float]:
    """A unit vector with 1 on a single axis. Orthogonal, the cosine distance is exactly 1."""
    vector = [0.0] * vectors.DIM
    vector[index] = 1.0
    return vector


MANIFEST = {
    "model": vectors.MODEL,
    "revision": "revsha",
    "doc_prefix": vectors.DOC_PREFIX,
    "query_prefix": vectors.QUERY_PREFIX,
    "l2_normalized": True,
    "dtype": "float32",
    "dim": vectors.DIM,
}


@pytest.fixture
def store(tmp_path):
    out = tmp_path / "vectors" / "e5base"
    rows = [("d1#0", "youtube_comment"), ("d2#0", "commerce_review"), ("d3#0", "youtube_comment")]
    vectors.save(out, np.array([_unit(0), _unit(1), _unit(2)], dtype="float32"), rows, MANIFEST)
    return out


def test_rrf_prefers_what_both_rankings_agree_on():
    # b, second on both sides, has to beat a, which is first on one side and absent from the other, for the
    # fusion to mean anything.
    fused = vectors.rrf(["a", "b", "c"], ["x", "b", "y"])
    assert fused[0] == "b"
    assert fused.index("b") < fused.index("a")


def test_rrf_is_deterministic_on_ties():
    # Leaning on dict order makes the same input give a different answer from run to run.
    assert vectors.rrf(["a", "b"], ["a", "b"]) == vectors.rrf(["a", "b"], ["a", "b"])
    assert vectors.rrf(["a"], ["b"]) == ["a", "b"]


def test_saving_leaves_three_files_that_belong_together(store):
    matrix, ids, manifest = vectors.paths(store)
    assert matrix.exists() and ids.exists() and manifest.exists()
    assert json.loads(manifest.read_text(encoding="utf-8"))["count"] == 3


def test_a_missing_store_says_what_to_run(tmp_path):
    with pytest.raises(vectors.StoreMissing) as refused:
        vectors.load(tmp_path / "nope")
    assert "embed" in str(refused.value)


def test_saving_refuses_when_rows_and_matrix_disagree(tmp_path):
    with pytest.raises(ValueError):
        vectors.save(tmp_path / "x", np.zeros((2, vectors.DIM), dtype="float32"), [("a", "s")], MANIFEST)


def test_loading_refuses_when_the_id_count_drifts(store):
    # The matrix and the ids correspond by order alone. A length mismatch cannot be noticed from a search
    # result.
    _, ids, _ = vectors.paths(store)
    ids.write_text("chunk_id,source\nd1#0,youtube_comment\n", encoding="utf-8")
    with pytest.raises(vectors.StoreMissing):
        vectors.load(store)


def test_loading_refuses_vectors_that_are_not_normalised(store):
    # Without normalization the inner product cannot serve as cosine. Rather than emit a quietly wrong
    # ranking, it stops.
    _, _, manifest = vectors.paths(store)
    manifest.write_text(json.dumps({**MANIFEST, "l2_normalized": False, "count": 3}), encoding="utf-8")
    with pytest.raises(vectors.StoreMissing):
        vectors.load(store)


@pytest.mark.parametrize("key", ["model", "query_prefix", "l2_normalized", "dim"])
def test_loading_refuses_a_manifest_that_is_missing_a_key(store, key):
    """Filling a missing key from a code default lets a store baked with another model or another prefix pass
    quietly, and that mismatch shows up as a wrong ranking rather than an error (#17 S7)."""
    _, _, manifest = vectors.paths(store)
    kept = {k: v for k, v in {**MANIFEST, "count": 3}.items() if k != key}
    manifest.write_text(json.dumps(kept), encoding="utf-8")
    with pytest.raises(vectors.StoreMissing) as refused:
        vectors.load(store)
    assert key in str(refused.value)


def test_loading_refuses_a_manifest_whose_dim_is_not_the_matrix_width(store):
    # dim was only written down and nobody compared it -- a 512-dimension matrix labelled 768 passed.
    _, _, manifest = vectors.paths(store)
    manifest.write_text(json.dumps({**MANIFEST, "dim": 512, "count": 3}), encoding="utf-8")
    with pytest.raises(vectors.StoreMissing) as refused:
        vectors.load(store)
    assert "512" in str(refused.value)


def test_loading_refuses_vectors_whose_rows_are_not_unit_length(tmp_path):
    # l2_normalized is a literal True embed.py wrote and cannot prove itself -- it is measured (#17 S8).
    out = tmp_path / "raw"
    matrix = np.zeros((2, vectors.DIM), dtype="float32")
    matrix[:, 0] = 3.0
    vectors.save(out, matrix, [("d1#0", "youtube_comment"), ("d2#0", "youtube_comment")], MANIFEST)
    with pytest.raises(vectors.StoreMissing) as refused:
        vectors.load(out)
    assert "노름" in str(refused.value)


def test_the_model_name_comes_from_the_manifest_alone(store):
    # The manifest is canonical. Falling back to code constants burns an e5 query on vectors baked with
    # another model.
    _, _, manifest = vectors.paths(store)
    manifest.write_text(json.dumps({**MANIFEST, "model": "other/model", "count": 3}), encoding="utf-8")
    assert vectors.load(store).model == "other/model"


def test_the_query_prefix_comes_from_the_manifest(store):
    # It has to pair with what was attached to the documents. Using the code default again makes two
    # canonical copies.
    _, _, manifest = vectors.paths(store)
    manifest.write_text(json.dumps({**MANIFEST, "query_prefix": "q: ", "count": 3}), encoding="utf-8")
    assert vectors.load(store).query_prefix == "q: "


def test_a_query_vector_of_the_wrong_width_is_refused(store):
    with pytest.raises(ValueError):
        vectors.search(vectors.load(store), [0.0, 1.0])


def test_search_returns_the_nearest_chunk_first(store):
    hits = vectors.search(vectors.load(store), _unit(1), top=3)
    assert hits[0][0] == "d2#0"
    assert hits[0][1] == pytest.approx(0.0, abs=1e-6)
    # An orthogonal axis is at cosine distance 1.
    assert all(h[1] == pytest.approx(1.0, abs=1e-6) for h in hits[1:])


def test_search_can_be_narrowed_to_one_source(store):
    hits = vectors.search(vectors.load(store), _unit(1), top=5, sources=("youtube_comment",))
    assert {h[0] for h in hits} == {"d1#0", "d3#0"}


def test_search_on_an_empty_store_returns_nothing(tmp_path):
    out = tmp_path / "empty"
    vectors.save(out, np.zeros((0, vectors.DIM), dtype="float32"), [], MANIFEST)
    assert vectors.search(vectors.load(out), _unit(0)) == []


def test_chunked_at_max_is_not_a_required_key(store):
    """A production store (encoded 2026-08-24) does not have this key -- raised to a required key, every
    vector and hybrid search running today becomes StoreMissing. It is a place to say it is missing when the
    coverage guard has none (#12)."""
    assert "chunked_at_max" not in vectors.REQUIRED_MANIFEST
    assert vectors.load(store).manifest.get("chunked_at_max") is None


def test_the_stamp_says_what_the_store_was_baked_from(store):
    """A different axis from the coverage warning -- that one has something to say only when out of step, and
    the revision always has (#49)."""
    stamped = vectors.load(store).stamp
    assert f"model={vectors.MODEL}" in stamped
    assert "revision=revsha" in stamped
    assert "vectors=3" in stamped


def test_the_stamp_tells_an_absent_key_from_a_null_value(store):
    """A missing key (a store baked before that key) and None (an empty corpus was burned) are different
    facts -- lumped into one word, a production store reads as an empty corpus."""
    assert "chunked_at_max=키없음" in vectors.manifest_stamp(MANIFEST, 3)
    assert "chunked_at_max=null" in vectors.manifest_stamp({**MANIFEST, "chunked_at_max": None}, 3)
    stamped = vectors.manifest_stamp({**MANIFEST, "chunked_at_max": "2026-08-19T09:00:00+09:00"}, 3)
    assert "chunked_at_max=2026-08-19T09:00:00+09:00" in stamped


def test_a_store_without_a_model_has_no_version_to_stamp():
    """A revision with only `model=` is not a revision -- rather than emit such a row it stops (`load` refuses
    in the same place)."""
    with pytest.raises(ValueError):
        vectors.manifest_stamp({**MANIFEST, "model": "  "}, 3)


def test_a_count_nobody_measured_is_not_written_as_zero():
    """Writing 0 into a manifest whose count is unknown reads as "an empty store" -- what is unknown is
    written as unknown."""
    assert "count" not in MANIFEST
    assert "vectors=미상" in vectors.manifest_stamp(MANIFEST)


def _stamp_tool(*args: str) -> subprocess.CompletedProcess[str]:
    # Python called on a missing file also exits 2 -- without checking the tool is there first, that is
    # indistinguishable from blocked.
    assert STAMP_TOOL.exists(), STAMP_TOOL
    return subprocess.run(
        [sys.executable, str(STAMP_TOOL), *args], capture_output=True, text=True, check=False
    )


def test_the_tool_prints_the_stamp_without_opening_the_matrix(store):
    """If the revision can only be known by opening 1.2GB, nobody ever re-checks the revision the contract
    writes down."""
    matrix, _, _ = vectors.paths(store)
    matrix.unlink()
    done = _stamp_tool(str(store))
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == vectors.manifest_stamp(MANIFEST, 3)


def test_the_tool_counts_the_ids_instead_of_quoting_the_manifest(store):
    """Copy the manifest's `count` as it is and that number is not measured but what the store claims."""
    _, _, manifest = vectors.paths(store)
    manifest.write_text(json.dumps({**MANIFEST, "count": 300_000}), encoding="utf-8")
    done = _stamp_tool(str(store))
    assert done.returncode == 2, done.stdout
    assert not done.stdout.strip() and "3" in done.stderr


def test_the_tool_is_blocked_when_the_store_is_not_readable(tmp_path, store):
    """An unreadable store is a block rather than a failure -- the same place as blocked in `entrypoints.md`
    §Search."""
    gone = _stamp_tool(str(tmp_path / "없다"))
    assert gone.returncode == 2 and gone.stderr.strip() and not gone.stdout.strip()
    _, _, manifest = vectors.paths(store)
    manifest.write_text(
        json.dumps({k: v for k, v in MANIFEST.items() if k != "model"} | {"count": 3}), encoding="utf-8"
    )
    keyless = _stamp_tool(str(store))
    assert keyless.returncode == 2 and "model" in keyless.stderr
