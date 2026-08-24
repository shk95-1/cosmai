#!/usr/bin/env python3
"""청크를 벡터로 만든다. **한 사람이 한 번에 돌린다.**

왜 한 번에. 나눠서 인코딩하면 모델 리비전·프리픽스·L2 정규화·dtype·텍스트 정규화·
입력 필드 **여섯 개가 하나만 어긋나도** 벡터를 합칠 수 없다. 그런데 어긋나도
**오류가 안 난다** — 코사인 유사도는 숫자가 나오고 순위만 조용히 엉뚱해진다.
그래서 이 스크립트는 설정을 전부 매니페스트에 적고, 합칠 때 그걸 대조한다.

무엇을 인코딩하나. **자유 텍스트만이다.**

    유튜브 댓글·영상 설명   278,916 청크   ← 이름 없는 불만이 여기 있다
    커머스 리뷰 본문         18,476 청크   ← 백탁 552 · 눈시림 252 · 따가 266
    성분·식약처              제외          ← 성분명은 정확 일치가 정답이다

성분명을 넣지 않는 이유. `에칠헥실트리아존` 을 벡터에 넣으면
`에칠헥실메톡시신나메이트` 도 비슷하다고 나온다. **성분이 다른데 비슷하다고 하면
그건 순위 문제가 아니라 오답이다.** 그쪽은 BM25 가 맡는다(성분 사전 적용 후
성분명 1,877종이 100% 한 토큰으로 나온다).

프리픽스. e5 계열은 문서에 `passage: `, 질의에 `query: ` 를 붙여야 한다.
안 붙이면 **에러 없이 성능만 떨어진다.** 그래서 기본값으로 박아 두고 매니페스트에 적는다.

저장. `{out}.npy`(float32 행렬) + `{out}.ids.csv`(같은 순서의 chunk_id) +
`{out}.manifest.json`. 벡터와 id 가 **순서로만** 대응하므로 길이를 검사하고,
매니페스트에 개수를 적어 둔다.

사용법 (GPU 머신에서):
    pip install sentence-transformers
    python encode_chunks.py --chunks reports/chunks_youtube.csv \
                            --chunks reports/chunks_commerce.csv \
                            --out .cache/vectors/e5base
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np

csv.field_size_limit(10 ** 8)

MODEL = "intfloat/multilingual-e5-base"
DOC_PREFIX = "passage: "     # e5 규약. 바꾸면 질의 프리픽스도 같이 바꿔야 한다
QUERY_PREFIX = "query: "
BATCH = 64                   # VRAM 8GB 기준. 부족하면 줄인다


def load(paths: list[Path]) -> tuple[list[str], list[str]]:
    """(chunk_id, text). 순서가 곧 벡터 순서다."""
    ids, texts = [], []
    for path in paths:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                ids.append(row["chunk_id"])
                texts.append(row["text"])
    return ids, texts


def model_revision(name: str) -> str:
    """모델 리비전 해시. 이름만 적으면 갱신본이 내려와도 이름이 같다."""
    try:
        from huggingface_hub import model_info
        return model_info(name).sha or "unknown"
    except Exception as error:      # 오프라인이거나 hub 가 없을 수 있다
        print(f"[경고] 리비전을 못 읽었다({error}). 매니페스트에 unknown 으로 적는다")
        return "unknown"


def encoder(name: str, device: str | None):
    """SentenceTransformer 를 늦게 불러온다. 없으면 무엇을 깔아야 하는지 알려준다."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        raise SystemExit(
            "sentence-transformers 가 없다. GPU 머신에서:\n"
            "    pip install sentence-transformers\n"
            "설정 점검만 하려면 --demo 를 쓴다")
    return SentenceTransformer(name, device=device)


def normalize_rows(matrix: np.ndarray) -> np.ndarray:
    """L2 정규화. 이러면 코사인 유사도가 내적과 같아져 검색이 행렬곱 한 번이 된다."""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0        # 빈 벡터가 있어도 0으로 나누지 않는다
    return (matrix / norms).astype(np.float32)


def run(chunks: list[Path], out: Path, name: str, batch: int,
        device: str | None, no_normalize: bool) -> int:
    missing = [p for p in chunks if not p.exists()]
    if missing:
        raise SystemExit(f"청크 파일이 없다: {', '.join(map(str, missing))}")

    ids, texts = load(chunks)
    print(f"청크 {len(ids):,}개 · 파일 {len(chunks)}개")
    lengths = sorted(len(t) for t in texts)
    print(f"길이 중앙 {lengths[len(lengths) // 2]}자 · 최대 {lengths[-1]}자")
    if lengths[-1] > 500:
        print(f"[경고] 500자를 넘는 청크가 있다(최대 {lengths[-1]}자). "
              f"e5 상한 512토큰을 넘으면 에러 없이 뒤가 잘린다")

    revision = model_revision(name)
    model = encoder(name, device)
    vectors = model.encode([DOC_PREFIX + t for t in texts], batch_size=batch,
                          show_progress_bar=True, convert_to_numpy=True)
    vectors = vectors.astype(np.float32)
    if not no_normalize:
        vectors = normalize_rows(vectors)

    if len(vectors) != len(ids):
        raise SystemExit(f"벡터 {len(vectors)} 와 id {len(ids)} 개수가 다르다")

    out.parent.mkdir(parents=True, exist_ok=True)
    np.save(out.with_suffix(".npy"), vectors)
    with out.with_suffix(".ids.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["chunk_id"])
        writer.writerows([[i] for i in ids])
    manifest = {
        "model": name,
        "revision": revision,
        "doc_prefix": DOC_PREFIX,
        "query_prefix": QUERY_PREFIX,
        "l2_normalized": not no_normalize,
        "dtype": "float32",
        "dim": int(vectors.shape[1]),
        "count": len(ids),
        "batch": batch,
        "chunk_files": {str(p): hashlib.sha256(p.read_bytes()).hexdigest()[:16]
                        for p in chunks},
    }
    out.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(json.dumps(manifest, ensure_ascii=False, indent=1))
    print()
    print(f"{out}.npy · .ids.csv · .manifest.json 저장")
    if revision == "unknown":
        print("[주의] 리비전이 unknown 이다. 나중에 이 벡터가 어느 모델에서 나왔는지 "
              "확인할 수 없으므로, 합치기 전에 반드시 맞춰야 한다")
    return 0


def demo() -> None:
    """모델 없이 점검한다. 여기서 잡는 건 설정 실수다 — 그게 조용히 틀리는 쪽이다."""
    # L2 정규화 후에는 모든 행의 노름이 1 이다
    raw = np.array([[3.0, 4.0], [1.0, 0.0], [0.0, 0.0]], dtype=np.float32)
    unit = normalize_rows(raw)
    assert abs(np.linalg.norm(unit[0]) - 1.0) < 1e-6, unit[0]
    assert abs(np.linalg.norm(unit[1]) - 1.0) < 1e-6
    assert np.allclose(unit[2], 0.0), "빈 벡터에서 0으로 나누면 nan 이 된다"
    assert unit.dtype == np.float32

    # 정규화하면 코사인 유사도가 내적과 같다. 검색이 행렬곱 한 번이 되는 근거
    a, b = normalize_rows(np.array([[1.0, 1.0]])), normalize_rows(np.array([[1.0, 0.0]]))
    assert abs(float((a @ b.T).ravel()[0]) - 0.7071) < 1e-3, (a @ b.T)

    # 프리픽스는 문서와 질의가 달라야 한다. 같게 두면 성능만 조용히 떨어진다
    assert DOC_PREFIX != QUERY_PREFIX
    assert DOC_PREFIX.endswith(" ") and QUERY_PREFIX.endswith(" ")

    # id 와 텍스트는 순서가 대응해야 한다
    import io as _io
    sample = "chunk_id,doc_id,source,ordinal,text\na#0,a,s,0,백탁\nb#0,b,s,0,촉촉\n"
    rows = list(csv.DictReader(_io.StringIO(sample)))
    assert [r["chunk_id"] for r in rows] == ["a#0", "b#0"]
    print("demo ok")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--chunks", action="append", type=Path, default=[])
    p.add_argument("--out", type=Path, default=Path(".cache/vectors/e5base"))
    p.add_argument("--model", default=MODEL)
    p.add_argument("--batch", type=int, default=BATCH)
    p.add_argument("--device", default=None, help="cuda / cpu. 없으면 자동")
    p.add_argument("--no-normalize", action="store_true",
                   help="L2 정규화를 끈다. 끄면 검색 쪽도 같이 맞춰야 한다")
    p.add_argument("--demo", action="store_true")
    a = p.parse_args()
    if a.demo:
        demo()
        return 0
    chunks = a.chunks or [Path("reports/chunks_youtube.csv"),
                          Path("reports/chunks_commerce.csv")]
    return run(chunks, a.out, a.model, a.batch, a.device, a.no_normalize)


if __name__ == "__main__":
    raise SystemExit(main())
