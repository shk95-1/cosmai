"""검색 평가 (slices/ydc/retrieval_eval.py). 검색기가 좋은지 나쁜지를 재는 채점기다.

정답은 공짜로 있다 -- `topics.match_topics` 가 청크 본문에 붙인 주제가 (문서, 주제) 라벨이다.
사람이 라벨을 달 필요가 없다. ydc 는 그것을 `common/mention.csv` 로 미리 내려 두었지만, 여기서는
청크가 DB 에 있으므로 읽으면서 만든다 -- 라벨 파일을 따로 두면 청크와 어긋난 채로 굳는다.

두 모드가 서로 다른 질문이다.

  literal   질의 = 주제 별칭 하나, 정답 = 그 주제가 붙은 문서 전부.
            **고장 감지용이다.** 정답 자체가 문자열 매칭으로 만들어졌으니 BM25 가 당연히
            잘한다. 여기서 못하면 토큰화가 깨진 것이다(사전 미적용, 정규화 불일치).

  heldout   질의 = 별칭 A, 정답 = A 의 **토큰이 하나도 없는** 같은 주제 문서.
            **진짜 측정이다.** BM25 는 구조적으로 0 에 가깝게 나오는 것이 정상이고,
            그 0 이 벡터가 넘어야 하는 선이다(#28 단계 4의 채택 기준).

주의 -- 이 결과로 파라미터를 만지면 그때부터 이 숫자는 성능이 아니다. 자동 라벨은 손잡이를
고르는 데 쓰고, 사람이 만든 골든셋은 최종 보고에 한 번만 쓴다.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import psycopg

from analysis.retrieval import bm25
from analysis.retrieval.topics import TOPICS

K = 10
FIELDS = ("mode", "engine", "topic_id", "query", "gold_size", "retrieved", "p_at_k", "mrr", "hit", "note")
MODES = ("literal", "heldout")
ENGINES = ("bm25", "vector", "hybrid")

# `cache_dir=None` 은 "캐시를 쓰지 마라"여야 한다. None 을 "기본값"으로 읽으면 끌 방법이 없고,
# 테스트가 레포의 var/retrieval/bm25 에 색인을 남긴다(2026-08-25 에 실제로 남겼다).
# 그래서 "안 넘겼음"을 나타내는 표식을 따로 둔다.
_DEFAULT_CACHE = Path("<default>")

GOLD_PAGE = 2000  # 정답을 만들 때 한 번에 물어 오는 청크 수 (corpus.BATCH 와 같은 규모)
GOLD_SQL = """
SELECT chunk_id, doc_id, text FROM retrieval_chunk
WHERE chunk_id > %s{source}
ORDER BY chunk_id
LIMIT %s
"""


def _cache(cache_dir: Path | None) -> Path | None:
    from analysis.retrieval.pipeline import CACHE_DIR

    return CACHE_DIR if cache_dir is _DEFAULT_CACHE else cache_dir


@dataclass(frozen=True)
class Row:
    mode: str
    engine: str
    topic_id: str
    query: str
    gold_size: int
    retrieved: int
    p_at_k: float
    mrr: float
    hit: bool
    note: str = ""  # 이 점수가 어느 코퍼스 위에서 나왔는지. 질의마다 같은 값이라 CSV 어느 줄을 봐도 읽힌다


def queries(mode: str) -> list[tuple[str, str]]:
    """(topic_id, 질의). 질의는 주제 별칭이다 -- 사람이 라벨을 만들지 않는다."""
    out = []
    for entry in TOPICS:
        if not entry["trend_use"]:
            continue  # 판정에 안 쓰는 주제는 평가에서도 뺀다
        aliases = entry["ko"] + entry["latin"]
        if mode == "heldout" and len(aliases) < 2:
            continue  # 별칭이 하나면 뺄 게 없다 (혼합자차)
        for alias in aliases:
            out.append((entry["topic"], alias))
    return out


def gold_from_chunks(conn: psycopg.Connection, sources: tuple[str, ...] | None = None) -> dict[str, set[str]]:
    """topic_id -> doc_id 집합. 청크 본문에 match_topics 를 돌려 만든다.

    문서 단위로 접는다 -- 정답이 chunk_id 면 한 문서의 조각 수가 점수를 좌우한다.

    `sources` 는 색인·검색과 같은 판으로 좁힌다 -- 좁힌 소스 밖의 문서는 어떤 엔진으로도
    나올 수 없으므로, 정답에 남으면 P@k·Hit@k 를 깎고 `gold_size` 가 틀린다.

    한 페이지씩 키셋으로 훑고 페이지마다 커밋한다(`corpus._keyset` 과 같은 방식). 서버 커서로 38만
    행을 한 흐름에 훑으면 그 트랜잭션이 매칭이 끝날 때까지 열려 있는데, needs_runtime 의
    `transaction_timeout`(60초, db/bootstrap.sql:48)은 트랜잭션 **총 수명**의 상한이라 도중에 끊는다.
    주제 매칭은 커밋한 뒤에 도는 것도 그래서다 -- 느린 쪽이 트랜잭션 밖에 있어야 한다.
    """
    from analysis.retrieval.topics import match_topics

    narrow, params = "", ()
    if sources:
        narrow, params = " AND source = ANY(%s)", (list(sources),)
    gold: dict[str, set[str]] = defaultdict(set)
    cursor = ""  # 마지막 행의 chunk_id 가 다음 페이지의 커서다
    while True:
        with conn.cursor() as cur:
            cur.execute(GOLD_SQL.format(source=narrow), (cursor, *params, GOLD_PAGE))  # noqa: S608
            rows = cur.fetchall()
        conn.commit()
        for _chunk_id, doc_id, text in rows:
            for topic in match_topics(text):
                gold[topic].add(doc_id)
        if len(rows) < GOLD_PAGE:
            return gold
        cursor = rows[-1][0]


def docs_with_tokens(index: bm25.Index, query: str) -> set[str]:
    """질의 토큰이 하나라도 든 문서. heldout 의 정답에서 뺀다.

    부분문자열로 빼면 안 된다 -- `하얘서` 를 Kiwi 는 `하얗` 으로 주므로 글자로는 안 겹치는데
    토큰으로는 겹친다. 색인이 실제로 쓰는 단위로 빼야 두 검색기가 같은 판에서 겨룬다.
    """
    found: set[str] = set()
    for term in set(bm25.tokenize(query)):
        for i, _tf in index.postings.get(term, ()):
            found.add(index.doc_ids[i].rsplit("#", 1)[0])
    return found


def to_docs(chunk_ids: list[str], k: int = K) -> list[str]:
    """검색 결과를 문서 단위로. 이걸 안 하면 정답(doc_id)과 형식이 달라 점수가 항상 0 이다."""
    out: list[str] = []
    seen: set[str] = set()
    for chunk_id in chunk_ids:
        doc_id = chunk_id.rsplit("#", 1)[0]
        if doc_id in seen:
            continue
        seen.add(doc_id)
        out.append(doc_id)
        if len(out) >= k:
            break
    return out


def score(ranked: list[str], gold: set[str]) -> tuple[float, float, bool]:
    """(P@k, MRR@k, Hit@k). 정답이 비면 그 질의는 건너뛰어야 하므로 호출 전에 막는다."""
    hits = [i for i, doc in enumerate(ranked, 1) if doc in gold]
    p = len(hits) / len(ranked) if ranked else 0.0
    mrr = 1 / hits[0] if hits else 0.0
    return p, mrr, bool(hits)


def run(
    conn: psycopg.Connection,
    mode: str,
    *,
    engine: str = "bm25",
    sources: tuple[str, ...] | None = None,
    store: Path | None = None,
    cache_dir: Path | None = _DEFAULT_CACHE,
    k: int = K,
) -> list[Row]:
    """질의마다 한 행. 색인은 heldout 의 정답 계산에도 쓰이므로 엔진과 무관하게 항상 만든다."""
    if mode not in MODES:
        raise ValueError(f"mode 는 {MODES} 중 하나다: {mode!r}")
    if engine not in ENGINES:
        raise ValueError(f"engine 은 {ENGINES} 중 하나다: {engine!r}")

    # 색인은 heldout 의 정답 계산(질의 토큰이 든 문서 빼기)에도 쓰이므로 엔진과 무관하게 만든다.
    # 정답 정의가 어휘 기준이어야 세 검색기가 같은 판에서 겨룬다.
    from analysis.retrieval.pipeline import coverage_note, load_index, ranked_chunks

    index, _ = load_index(conn, sources, cache_dir=_cache(cache_dir))
    gold_all = gold_from_chunks(conn, sources)

    # 벡터 저장소와 모델은 여기서 한 번만 연다. 질의마다 열면 1.2GB 행렬과 모델을 61번 읽는다.
    vector_store = encoder = None
    coverage = ""
    if engine != "bm25":
        from analysis.retrieval import embed, vectors

        vector_store = vectors.load(store or vectors.DEFAULT_STORE)
        # 저장소를 연 쪽이 커버리지를 묻는다. 채점표에 실어야 "어느 코퍼스 위의 점수인가"가 읽힌다 --
        # 어긋나도 점수는 멀쩡한 숫자로 나오므로 수치만 봐서는 알 수 없다.
        coverage = coverage_note(conn, vector_store) or ""
        encoder = embed.load_encoder(vector_store.model)

    rows: list[Row] = []
    for topic_id, query in queries(mode):
        gold = set(gold_all.get(topic_id, ()))
        skip: set[str] = set()
        if mode == "heldout":
            skip = docs_with_tokens(index, query)
            gold -= skip
        if not gold:
            continue  # 정답이 없는 질의는 점수를 정의할 수 없다
        # 후보는 줄이지 않는다. 두 검색기가 같은 후보·같은 정답으로 겨뤄야 점수를 비교할 수 있다.
        hits = ranked_chunks(
            conn,
            query,
            engine=engine,
            top=k * 4,
            sources=sources,
            store=store,
            cache_dir=_cache(cache_dir),
            index=index,  # 위에서 세운 것을 넘긴다 -- 질의마다 다시 읽으면 61번 푼다
            vector_store=vector_store,
            encoder=encoder,
        )
        ranked = to_docs([c for c, _ in hits], k)
        p, mrr, hit = score(ranked, gold)
        rows.append(Row(mode, engine, topic_id, query, len(gold), len(ranked), p, mrr, hit, coverage))
    return rows


def summary(rows: list[Row]) -> str:
    """literal 이 0.9 밑이면 토큰화를 의심한다 -- 성능 보고가 아니라 고장 감지다."""
    if not rows:
        return "질의 0개 (청크가 비었는지 확인)"
    n = len(rows)
    p = sum(r.p_at_k for r in rows) / n
    mrr = sum(r.mrr for r in rows) / n
    hit = sum(1 for r in rows if r.hit) / n
    line = f"질의 {n}개 · P@{K} {p:.3f} · MRR@{K} {mrr:.3f} · Hit@{K} {hit:.0%}"
    # 커버리지 경고는 CSV 를 안 열어도 보여야 한다 -- 요약만 읽고 표에 옮겨 적는 것이 실제 용법이다.
    note = next((r.note for r in rows if r.note), "")
    return f"{line}\n{note}" if note else line
