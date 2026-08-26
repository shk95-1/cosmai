"""벡터 저장소와 RRF 융합 (slices/ydc/{encode_chunks,hybrid}.py).

**벡터는 파일에 둔다.** pgvector 는 다음으로 미뤘다 -- 확장을 얹으려면 공유 postgres 를 다시
띄워야 하고, 그 전에 "벡터가 BM25 를 넘는가"를 먼저 재는 편이 순서가 맞다. 저장 형식은 ydc 가
쓰던 것 그대로다.

    {out}.npy           float32 행렬 (행 수 = 청크 수, 열 = 768)
    {out}.ids.csv       같은 순서의 chunk_id 와 source
    {out}.manifest.json 무엇으로 만든 벡터인지

**매니페스트가 핵심이다.** 모델 리비전 · 프리픽스 · L2 정규화 · dtype 여섯 가지 중 하나만
어긋나도 벡터를 합칠 수 없는데, 어긋나도 **오류가 안 난다** -- 코사인 유사도는 숫자가 나오고
순위만 조용히 엉뚱해진다. 그래서 설정을 파일에 적고 읽을 때 대조한다.

행렬과 id 는 **순서로만** 대응한다. 그래서 길이를 검사하고 매니페스트에 개수를 적어 둔다.

RRF 를 쓰는 이유는 **점수 스케일이 다르기 때문**이다 -- BM25 는 11.83 같은 값이고 코사인은
0~1 이다. 정규화해서 더하면 그 정규화 방식이 또 하나의 손잡이가 된다. RRF 는 순위만 쓴다.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

RRF_K = 60  # 관행값. 우리 데이터로 다시 뽑지 않았다 -- 뽑으려면 자동 라벨로 골라야 한다
DIM = 768
MODEL = "intfloat/multilingual-e5-base"
DOC_PREFIX = "passage: "
QUERY_PREFIX = "query: "  # 안 붙이면 오류 없이 성능만 떨어진다
DEFAULT_STORE = Path("var/retrieval/vectors/e5base")
# 읽을 때 반드시 있어야 하는 설정. 없는 것을 코드 기본값으로 메우면 다른 모델·다른 프리픽스로 구운
# 저장소가 조용히 통과하고, 그 어긋남은 오류가 아니라 틀린 순위로만 나타난다.
REQUIRED_MANIFEST = ("model", "query_prefix", "l2_normalized", "dim")
UNIT_TOLERANCE = 1e-3  # float32 로 저장한 단위 벡터가 노름 1 에서 벗어나는 폭


class StoreMissing(RuntimeError):
    """벡터 파일이 없다. `cosmai retrieval embed` 를 아직 안 돌렸다는 뜻이다."""


def paths(out: Path) -> tuple[Path, Path, Path]:
    """(행렬, id, 매니페스트). 셋이 한 벌이라 한 자리에서 만든다."""
    return out.with_suffix(".npy"), out.with_suffix(".ids.csv"), out.with_suffix(".manifest.json")


@dataclass(frozen=True)
class VectorStore:
    """읽어 들인 벡터 한 벌. 30만 x 768 float32 = 약 0.9 GB 라 메모리에 그냥 둔다."""

    matrix: object  # numpy.ndarray -- numpy 를 모듈 최상단에서 끌어오지 않으려고 느슨하게 둔다
    chunk_ids: list[str]
    sources: list[str]
    manifest: dict

    @property
    def model(self) -> str:
        return str(self.manifest["model"])

    @property
    def stamp(self) -> str:
        """이 저장소가 어느 판본인지 한 줄. 개수는 매니페스트가 아니라 실제 id 수다."""
        return manifest_stamp(self.manifest, len(self.chunk_ids))

    @property
    def query_prefix(self) -> str:
        # 문서에 붙인 것과 짝이 맞아야 한다. load() 가 부재를 막으므로 여기서 기본값을 대지 않는다.
        return str(self.manifest["query_prefix"])


def manifest_stamp(manifest: dict, count: int | None = None) -> str:
    """무엇으로 구운 벡터인가 (포크 #49). 뼈대 -- 아직 아무 판본도 내지 않는다."""
    return ""


def save(out: Path, matrix, rows: list[tuple[str, str]], manifest: dict) -> None:
    """행렬·id·매니페스트를 한 벌로 쓴다. rows 는 (chunk_id, source) 이고 행렬과 순서가 같다."""
    import numpy as np

    if len(rows) != len(matrix):
        raise ValueError(f"행렬 {len(matrix)}행과 id {len(rows)}개가 다르다")
    matrix_path, ids_path, manifest_path = paths(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.save(matrix_path, np.asarray(matrix, dtype="float32"))
    with ids_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["chunk_id", "source"])
        writer.writerows(rows)
    manifest_path.write_text(
        json.dumps({**manifest, "count": len(rows)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load(out: Path = DEFAULT_STORE) -> VectorStore:
    """세 파일을 대조해서 읽는다. 하나라도 어긋나면 여기서 멈춘다 -- 검색 결과로는 못 알아챈다."""
    import numpy as np

    matrix_path, ids_path, manifest_path = paths(out)
    missing = [p.name for p in (matrix_path, ids_path, manifest_path) if not p.exists()]
    if missing:
        raise StoreMissing(
            f"벡터 파일이 없다: {', '.join(missing)} ({out.parent}). "
            "`cosmai retrieval embed` 를 먼저 돌려야 한다."
        )
    matrix = np.load(matrix_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    with ids_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != len(matrix):
        raise StoreMissing(f"행렬 {len(matrix)}행과 id {len(rows)}개가 다르다: {out}")
    if manifest.get("count") not in (None, len(rows)):
        raise StoreMissing(f"매니페스트가 {manifest['count']}개라는데 id 는 {len(rows)}개다: {out}")
    if absent := [key for key in REQUIRED_MANIFEST if key not in manifest]:
        raise StoreMissing(
            f"매니페스트에 {', '.join(absent)} 가 없다: {manifest_path}. "
            "`cosmai retrieval embed` 로 다시 만들어야 한다."
        )
    if not str(manifest["model"]).strip():
        raise StoreMissing(f"매니페스트의 model 이 비어 있다: {manifest_path}")
    if matrix.ndim != 2 or matrix.shape[1] != manifest["dim"]:
        raise StoreMissing(f"매니페스트가 {manifest['dim']} 차원이라는데 행렬은 {matrix.shape} 다: {out}")
    if not manifest["l2_normalized"]:
        # 정규화가 안 됐으면 내적을 코사인으로 쓸 수 없다. 조용히 틀린 순위를 내느니 멈춘다.
        raise StoreMissing(f"l2_normalized 가 아닌 벡터다: {manifest_path}")
    # 플래그는 인코딩 때 적은 리터럴이라 스스로를 증명하지 못한다 -- 행 하나를 재서 대조한다.
    norm = float(np.linalg.norm(matrix[0])) if len(matrix) else 1.0
    if abs(norm - 1.0) > UNIT_TOLERANCE:
        raise StoreMissing(f"l2_normalized 라는데 첫 행의 노름이 {norm:.3f} 다: {matrix_path}")
    return VectorStore(matrix, [r["chunk_id"] for r in rows], [r["source"] for r in rows], manifest)


def rrf(*rankings: list[str], k: int = RRF_K) -> list[str]:
    """여러 순위를 하나로. score(d) = sum 1 / (k + rank_i(d))."""
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, 1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
            first_seen.setdefault(item, rank)
    # 동점은 처음 등장한 순위로 가른다 -- dict 순서에 기대면 실행마다 답이 흔들린다.
    return sorted(scores, key=lambda item: (-scores[item], first_seen[item], item))


def search(
    store: VectorStore,
    query_vector,
    *,
    top: int = 10,
    sources: tuple[str, ...] | None = None,
) -> list[tuple[str, float]]:
    """(chunk_id, 코사인 거리). 거리이므로 작을수록 가깝다.

    벡터가 L2 정규화돼 있으므로 코사인 유사도는 내적이고, 거리는 1 - 내적이다.
    """
    import numpy as np

    vector = np.asarray(query_vector, dtype="float32")
    if vector.shape != (DIM,):
        raise ValueError(f"질의 벡터가 {DIM} 차원이 아니다: {vector.shape}")
    similarity = np.asarray(store.matrix) @ vector
    if sources:
        wanted = set(sources)
        # 제외는 -inf 로. 행을 걸러내면 인덱스가 밀려 chunk_id 대응이 깨진다.
        mask = np.array([s in wanted for s in store.sources])
        similarity = np.where(mask, similarity, -np.inf)
    take = min(top, len(similarity))
    if take == 0:
        return []
    top_idx = np.argpartition(-similarity, take - 1)[:take]
    top_idx = top_idx[np.argsort(-similarity[top_idx], kind="stable")]
    return [(store.chunk_ids[i], float(1.0 - similarity[i])) for i in top_idx if np.isfinite(similarity[i])]
