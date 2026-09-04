"""The vector store and RRF fusion (ydc encode_chunks.py · hybrid.py, v0.1.0 02440ab).

**The vectors live in a file.** pgvector was put off to next time -- taking the extension would mean bringing
the shared postgres up again, and measuring "do the vectors beat BM25" first is the right order. The storage
format is the one that was already in use.

    {out}.npy           float32 matrix (rows = chunks, columns = 768)
    {out}.ids.csv       chunk_id and source in the same order
    {out}.manifest.json what the vectors were made with

**The manifest is the point.** One of the six -- model revision · prefix · L2 normalization · dtype -- being
out of step makes the vectors impossible to merge, and being out of step raises **no error** -- cosine
similarity still gives a number and only the ranking goes quietly wrong. So the settings are written to the
file and checked against on read.

The matrix and the ids correspond **by order alone**. So the lengths are checked and the count is written
into the manifest.

RRF is used because **the score scales differ** -- BM25 is a value like 11.83 and cosine is 0 to 1.
Normalizing and adding makes the normalization method one more knob. RRF looks only at the ranks.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

RRF_K = 60  # the conventional value. Not re-derived on our data -- deriving it would need automatic labels
DIM = 768
MODEL = "intfloat/multilingual-e5-base"
DOC_PREFIX = "passage: "
QUERY_PREFIX = "query: "  # without it the performance just drops, with no error
DEFAULT_STORE = Path("var/retrieval/vectors/e5base")
# The settings that must be present on read. Filling a missing one from a code default lets a store from
# another model or another setting pass quietly, and that mismatch shows up as a wrong ranking, not an error.
REQUIRED_MANIFEST = ("model", "query_prefix", "l2_normalized", "dim")
UNIT_TOLERANCE = 1e-3  # how far a unit vector stored as float32 may stray from norm 1


class StoreMissing(RuntimeError):
    """There is no vector file. It means `cosmai retrieval embed` has not been run yet."""


def paths(out: Path) -> tuple[Path, Path, Path]:
    """(matrix, ids, manifest). The three are one set, so they are made in one place."""
    return out.with_suffix(".npy"), out.with_suffix(".ids.csv"), out.with_suffix(".manifest.json")


@dataclass(frozen=True)
class VectorStore:
    """One loaded set of vectors. 300k x 768 float32 = about 0.9 GB, so it just stays in memory."""

    matrix: object  # numpy.ndarray -- kept loose so numpy is not pulled in at module top level
    chunk_ids: list[str]
    sources: list[str]
    manifest: dict

    @property
    def model(self) -> str:
        return str(self.manifest["model"])

    @property
    def stamp(self) -> str:
        """One line saying which revision this store is. The count is the real id count, not the
        manifest's."""
        return manifest_stamp(self.manifest, len(self.chunk_ids))

    @property
    def query_prefix(self) -> str:
        # It has to pair with what was attached to the documents. load() blocks an absence, so no default is
        # put here.
        return str(self.manifest["query_prefix"])


def manifest_stamp(manifest: dict, count: int | None = None) -> str:
    """One line saying what the vectors were baked from (fork #49). Without `count` it says what the manifest
    says.

    It is on **a different axis** from `pipeline.coverage_note`. That one is "is it out of step with the
    current chunks", so it has nothing to say when it is not; this one is "what were the vectors baked from",
    so it always has something to say -- when everything is normal the revision is what it says. In ydc, a
    delta labelled "first pass -> second pass" was really "no MFDS vectors -> second pass"; this is that
    place.

    `chunked_at_max` missing and `chunked_at_max` None are different facts and are written as different
    words -- missing means a store baked before the field existed, None means an empty corpus was burned.
    """
    model = str(manifest.get("model", "")).strip()
    if not model:
        # A revision with no model is not a revision. Rather than emit such a row it stops here (`load` does
        # the same).
        raise ValueError("매니페스트에 model 이 없다 -- 판본을 적을 수 없다")
    total = manifest.get("count") if count is None else count
    parts = [f"model={model}"]
    if revision := str(manifest.get("revision", "")).strip():
        parts.append(f"revision={revision}")
    parts.append(f"vectors={total if total is not None else '미상'}")
    if "chunked_at_max" not in manifest:
        moment = "키없음"
    else:
        moment = manifest["chunked_at_max"] or "null"
    parts.append(f"chunked_at_max={moment}")
    return " · ".join(parts)


def save(out: Path, matrix, rows: list[tuple[str, str]], manifest: dict) -> None:
    """Writes matrix, ids and manifest as one set. rows is (chunk_id, source) and is in the same order as the
    matrix."""
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
    """Reads the three files against each other. One mismatch and it stops here -- a search result would not
    show it."""
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
        # Without normalization the inner product cannot serve as cosine. Rather than emit a quietly wrong
        # ranking, it stops.
        raise StoreMissing(f"l2_normalized 가 아닌 벡터다: {manifest_path}")
    # The flag is a literal written at encoding time and cannot prove itself -- one row is measured instead.
    norm = float(np.linalg.norm(matrix[0])) if len(matrix) else 1.0
    if abs(norm - 1.0) > UNIT_TOLERANCE:
        raise StoreMissing(f"l2_normalized 라는데 첫 행의 노름이 {norm:.3f} 다: {matrix_path}")
    return VectorStore(matrix, [r["chunk_id"] for r in rows], [r["source"] for r in rows], manifest)


def rrf(*rankings: list[str], k: int = RRF_K) -> list[str]:
    """Several rankings into one. score(d) = sum 1 / (k + rank_i(d))."""
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, 1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
            first_seen.setdefault(item, rank)
    # A tie is broken by the rank it first appeared at -- leaning on dict order makes the answer shake from
    # run to run.
    return sorted(scores, key=lambda item: (-scores[item], first_seen[item], item))


def search(
    store: VectorStore,
    query_vector,
    *,
    top: int = 10,
    sources: tuple[str, ...] | None = None,
) -> list[tuple[str, float]]:
    """(chunk_id, cosine distance). It is a distance, so smaller is closer.

    The vectors are L2-normalized, so cosine similarity is the inner product and the distance is 1 - it.
    """
    import numpy as np

    vector = np.asarray(query_vector, dtype="float32")
    if vector.shape != (DIM,):
        raise ValueError(f"질의 벡터가 {DIM} 차원이 아니다: {vector.shape}")
    similarity = np.asarray(store.matrix) @ vector
    if sources:
        wanted = set(sources)
        # Exclusions go to -inf. Filtering rows out shifts the indexes and breaks the chunk_id correspondence.
        mask = np.array([s in wanted for s in store.sources])
        similarity = np.where(mask, similarity, -np.inf)
    take = min(top, len(similarity))
    if take == 0:
        return []
    top_idx = np.argpartition(-similarity, take - 1)[:take]
    top_idx = top_idx[np.argsort(-similarity[top_idx], kind="stable")]
    return [(store.chunk_ids[i], float(1.0 - similarity[i])) for i in top_idx if np.isfinite(similarity[i])]
