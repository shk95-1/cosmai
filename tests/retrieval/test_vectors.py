"""파일 벡터 저장소와 RRF. 모델은 부르지 않는다 -- 여기서 재는 것은 "세 파일이 한 벌로 남는가 ·
어긋나면 멈추는가 · 코사인 순서로 나오는가" 다. 어긋난 벡터는 오류가 아니라 틀린 순위로
나타나므로, 읽는 쪽에서 세우지 않으면 아무도 못 알아챈다."""

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


@pytest.mark.parametrize("key", ["model", "query_prefix", "l2_normalized", "dim"])
def test_loading_refuses_a_manifest_that_is_missing_a_key(store, key):
    """빠진 키를 코드의 기본값으로 메우면 다른 모델·다른 프리픽스로 구운 저장소가 조용히
    통과하고, 그 어긋남은 오류가 아니라 틀린 순위로만 나타난다(#17 S7)."""
    _, _, manifest = vectors.paths(store)
    kept = {k: v for k, v in {**MANIFEST, "count": 3}.items() if k != key}
    manifest.write_text(json.dumps(kept), encoding="utf-8")
    with pytest.raises(vectors.StoreMissing) as refused:
        vectors.load(store)
    assert key in str(refused.value)


def test_loading_refuses_a_manifest_whose_dim_is_not_the_matrix_width(store):
    # dim 은 적어 두기만 하고 아무도 대조하지 않았다 -- 768 이라 적힌 512 차원 행렬이 통과했다.
    _, _, manifest = vectors.paths(store)
    manifest.write_text(json.dumps({**MANIFEST, "dim": 512, "count": 3}), encoding="utf-8")
    with pytest.raises(vectors.StoreMissing) as refused:
        vectors.load(store)
    assert "512" in str(refused.value)


def test_loading_refuses_vectors_whose_rows_are_not_unit_length(tmp_path):
    # l2_normalized 는 embed.py 가 적은 리터럴 True 라 스스로를 증명하지 못한다 -- 재서 본다(#17 S8).
    out = tmp_path / "raw"
    matrix = np.zeros((2, vectors.DIM), dtype="float32")
    matrix[:, 0] = 3.0
    vectors.save(out, matrix, [("d1#0", "youtube_comment"), ("d2#0", "youtube_comment")], MANIFEST)
    with pytest.raises(vectors.StoreMissing) as refused:
        vectors.load(out)
    assert "노름" in str(refused.value)


def test_the_model_name_comes_from_the_manifest_alone(store):
    # 매니페스트가 정본이다. 코드 상수로 되돌아가면 다른 모델로 구운 벡터에 e5 질의를 태운다.
    _, _, manifest = vectors.paths(store)
    manifest.write_text(json.dumps({**MANIFEST, "model": "other/model", "count": 3}), encoding="utf-8")
    assert vectors.load(store).model == "other/model"


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


def test_chunked_at_max_is_not_a_required_key(store):
    """운영 저장소(2026-08-24 인코딩분)에는 이 키가 없다 -- 필수 키로 올리면 지금 도는 vector·hybrid
    검색이 통째로 StoreMissing 이 된다. 커버리지 가드가 없으면 없다고 말할 자리다(#12)."""
    assert "chunked_at_max" not in vectors.REQUIRED_MANIFEST
    assert vectors.load(store).manifest.get("chunked_at_max") is None


def test_the_stamp_says_what_the_store_was_baked_from(store):
    """커버리지 경고와 축이 다르다 -- 그쪽은 어긋날 때만 할 말이 있고, 판본은 언제나 있다(#49)."""
    stamped = vectors.load(store).stamp
    assert f"model={vectors.MODEL}" in stamped
    assert "revision=revsha" in stamped
    assert "vectors=3" in stamped


def test_the_stamp_tells_an_absent_key_from_a_null_value(store):
    """키가 없는 것(그 키 이전에 구운 저장소)과 None 인 것(빈 코퍼스를 태웠다)은 다른 사실이다 --
    한 낱말로 뭉치면 운영 저장소가 빈 코퍼스로 읽힌다."""
    assert "chunked_at_max=키없음" in vectors.manifest_stamp(MANIFEST, 3)
    assert "chunked_at_max=null" in vectors.manifest_stamp({**MANIFEST, "chunked_at_max": None}, 3)
    stamped = vectors.manifest_stamp({**MANIFEST, "chunked_at_max": "2026-08-19T09:00:00+09:00"}, 3)
    assert "chunked_at_max=2026-08-19T09:00:00+09:00" in stamped


def test_a_store_without_a_model_has_no_version_to_stamp():
    """`model=` 만 적힌 판본은 판본이 아니다 -- 그런 행을 내느니 멈춘다(`load` 도 같은 자리에서 거절한다)."""
    with pytest.raises(ValueError):
        vectors.manifest_stamp({**MANIFEST, "model": "  "}, 3)


def test_a_count_nobody_measured_is_not_written_as_zero():
    """개수를 모르는 매니페스트에 0 을 적으면 "빈 저장소" 로 읽힌다 -- 모르는 것은 모른다고 적는다."""
    assert "count" not in MANIFEST
    assert "vectors=미상" in vectors.manifest_stamp(MANIFEST)


def _stamp_tool(*args: str) -> subprocess.CompletedProcess[str]:
    # 없는 파일을 부른 파이썬도 2 로 나간다 -- 도구가 있는지 먼저 보지 않으면 막힘과 구분되지 않는다.
    assert STAMP_TOOL.exists(), STAMP_TOOL
    return subprocess.run(
        [sys.executable, str(STAMP_TOOL), *args], capture_output=True, text=True, check=False
    )


def test_the_tool_prints_the_stamp_without_opening_the_matrix(store):
    """1.2GB 를 열어야 판본을 알 수 있으면 계약에 적힌 판본을 아무도 다시 확인하지 않는다."""
    matrix, _, _ = vectors.paths(store)
    matrix.unlink()
    done = _stamp_tool(str(store))
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == vectors.manifest_stamp(MANIFEST, 3)


def test_the_tool_counts_the_ids_instead_of_quoting_the_manifest(store):
    """매니페스트의 `count` 를 그대로 옮기면 그 수는 잰 것이 아니라 저장소가 주장하는 것이다."""
    _, _, manifest = vectors.paths(store)
    manifest.write_text(json.dumps({**MANIFEST, "count": 300_000}), encoding="utf-8")
    done = _stamp_tool(str(store))
    assert done.returncode == 2, done.stdout
    assert not done.stdout.strip() and "3" in done.stderr


def test_the_tool_is_blocked_when_the_store_is_not_readable(tmp_path, store):
    """읽을 수 없는 저장소는 실패가 아니라 막힘이다 -- `entrypoints.md` §검색 의 blocked 와 같은 자리다."""
    gone = _stamp_tool(str(tmp_path / "없다"))
    assert gone.returncode == 2 and gone.stderr.strip() and not gone.stdout.strip()
    _, _, manifest = vectors.paths(store)
    manifest.write_text(
        json.dumps({k: v for k, v in MANIFEST.items() if k != "model"} | {"count": 3}), encoding="utf-8"
    )
    keyless = _stamp_tool(str(store))
    assert keyless.returncode == 2 and "model" in keyless.stderr
