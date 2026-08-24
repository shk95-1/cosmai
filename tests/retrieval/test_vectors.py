"""파일 벡터 저장소와 RRF. 모델은 부르지 않는다 -- 여기서 재는 것은 "세 파일이 한 벌로 남는가 ·
어긋나면 멈추는가 · 코사인 순서로 나오는가" 다. 어긋난 벡터는 오류가 아니라 틀린 순위로
나타나므로, 읽는 쪽에서 세우지 않으면 아무도 못 알아챈다."""

from __future__ import annotations

import json

import numpy as np
import pytest

from analysis.retrieval import vectors


def _unit(index: int) -> list[float]:
    """축 하나만 1 인 단위 벡터. 직교하면 코사인 거리가 정확히 1 이 된다."""
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
    # 양쪽에서 2위인 b 가, 한쪽에서만 1위이고 다른 쪽에는 없는 a 를 이겨야 융합이 의미가 있다.
    fused = vectors.rrf(["a", "b", "c"], ["x", "b", "y"])
    assert fused[0] == "b"
    assert fused.index("b") < fused.index("a")


def test_rrf_is_deterministic_on_ties():
    # dict 순서에 기대면 같은 입력이 실행마다 다른 답을 준다.
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
    # 행렬과 id 는 순서로만 대응한다. 길이가 어긋나면 검색 결과로는 못 알아챈다.
    _, ids, _ = vectors.paths(store)
    ids.write_text("chunk_id,source\nd1#0,youtube_comment\n", encoding="utf-8")
    with pytest.raises(vectors.StoreMissing):
        vectors.load(store)


def test_loading_refuses_vectors_that_are_not_normalised(store):
    # 정규화가 안 됐으면 내적을 코사인으로 쓸 수 없다. 조용히 틀린 순위를 내느니 멈춘다.
    _, _, manifest = vectors.paths(store)
    manifest.write_text(json.dumps({**MANIFEST, "l2_normalized": False, "count": 3}), encoding="utf-8")
    with pytest.raises(vectors.StoreMissing):
        vectors.load(store)


def test_the_query_prefix_comes_from_the_manifest(store):
    # 문서에 붙인 것과 짝이 맞아야 한다. 코드의 기본값을 다시 쓰면 정본이 두 벌이 된다.
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
    # 직교하는 축은 코사인 거리 1 이다.
    assert all(h[1] == pytest.approx(1.0, abs=1e-6) for h in hits[1:])


def test_search_can_be_narrowed_to_one_source(store):
    hits = vectors.search(vectors.load(store), _unit(1), top=5, sources=("youtube_comment",))
    assert {h[0] for h in hits} == {"d1#0", "d3#0"}


def test_search_on_an_empty_store_returns_nothing(tmp_path):
    out = tmp_path / "empty"
    vectors.save(out, np.zeros((0, vectors.DIM), dtype="float32"), [], MANIFEST)
    assert vectors.search(vectors.load(out), _unit(0)) == []
