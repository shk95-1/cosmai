"""원천 -> 청크 -> needs.retrieval_chunk, 그리고 그 청크 위에 세우는 BM25 검색.

배치마다 커밋한다. needs_runtime 의 transaction_timeout 이 60초라 30만 행을 한 트랜잭션에
담으면 끝까지 가지 못한다 -- analysis/aggregate·polarity 가 같은 이유로 같은 모양이다.
"""

from __future__ import annotations

import hashlib
import os
import pickle
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import psycopg

from analysis.retrieval import corpus
from analysis.retrieval.bm25 import TOKENIZER_INPUTS, Index
from analysis.retrieval.chunks import (
    MAX_CHARS,
    SAMPLES_PER_KIND,
    check_rows,
    problem_kind,
    split_text,
)
from analysis.retrieval.normalize import normalize_text

WRITE_BATCH = 1000

UPSERT = """
INSERT INTO retrieval_chunk (chunk_id, doc_id, source, ordinal, text, text_md5)
VALUES (%(chunk_id)s, %(doc_id)s, %(source)s, %(ordinal)s, %(text)s, %(text_md5)s)
ON CONFLICT (chunk_id) DO UPDATE SET
  text = EXCLUDED.text, text_md5 = EXCLUDED.text_md5, chunked_at = now()
WHERE retrieval_chunk.text_md5 IS DISTINCT FROM EXCLUDED.text_md5
"""

PRUNE = "DELETE FROM retrieval_chunk WHERE doc_id = %(doc_id)s AND ordinal >= %(ordinal)s"

# 문서를 통째로 지우고 다시 넣지는 않는다 -- 지웠다 넣으면 그 사이 검색이 빈다. 지우는 것은 원천이
# 짧아진 문서의 꼬리뿐이고(새 조각 수 이상의 ordinal), 그 꼬리가 남으면 "ordinal 은 0 부터 연속"
# (contracts/ddl/needs/020_retrieval_chunk.sql:15)이 표 수준에서 깨진다 -- 배치만 보는 check_rows 는
# 그 문서를 다시 다 봤으므로 위반을 못 낸다. 같은 트랜잭션에서 UPSERT 뒤에 돈다.


@dataclass(frozen=True)
class ChunkOutcome:
    documents: int
    chunks: int
    written: int
    problems: list[str]
    pruned: int = 0
    over_target: int = 0
    over_target_max: int = 0

    @property
    def note(self) -> str:
        head = f"문서 {self.documents:,} -> 청크 {self.chunks:,} (변경 {self.written:,})"
        if self.pruned:
            # 원천이 짧아졌다는 뜻이라 조용히 넘어갈 일이 아니다 -- 수집기는 추가만 한다.
            head += f"; 짧아진 문서의 꼬리 {self.pruned:,} 삭제"
        if self.over_target:
            # 하드스톱(1000자) 미만이라 problems 는 아니지만, "[통과]"가 500 위반 없음으로 읽혀
            # 남의 청크 27건이 묻힌 적이 있다(ydc v0.2.0) -- 몇 건인지는 항상 보여야 한다.
            head += f"; 목표 상한 초과 {self.over_target:,}건 (최대 {self.over_target_max:,}자)"
        if not self.problems:
            return head
        # problems 는 종류별 표본 몇 건이지 위반 건수가 아니다 -- 그 길이를 "종" 이라 부르면
        # 한 종류의 표본 3건이 "3종" 으로 읽힌다(#18 M12).
        kinds = len({problem_kind(p) for p in self.problems})
        return f"{head}; 계약 위반 {kinds}종"


def chunk_rows(documents: Iterable[corpus.Document]) -> Iterator[dict]:
    """문서 하나를 0개 이상의 청크 행으로. 빈 본문은 색인에 넣지 않는다."""
    for document in documents:
        pieces = split_text(normalize_text(document.text))
        for ordinal, piece in enumerate(pieces):
            yield {
                "chunk_id": f"{document.doc_id}#{ordinal}",
                "doc_id": document.doc_id,
                "source": document.source,
                "ordinal": ordinal,
                "text": piece,
                "text_md5": hashlib.md5(piece.encode()).hexdigest(),
            }


def run(
    conn: psycopg.Connection,
    *,
    youtube_schema: str = "tubedepth",
    commerce_schema: str = "trend_radar",
    since: date | None = None,
    sources: tuple[str, ...] = corpus.SOURCES,
) -> ChunkOutcome:
    """원천을 훑어 청크를 적재한다. 계약 위반은 세어서 돌려주되 적재를 막지는 않는다 --
    한 소스의 결함으로 나머지 세 소스의 색인이 통째로 비는 편이 더 나쁘다."""
    documents = corpus.documents(
        conn,
        youtube_schema=youtube_schema,
        commerce_schema=commerce_schema,
        since=since,
        sources=sources,
    )
    seen_docs: set[str] = set()
    total = written = pruned = over_target = over_target_max = 0
    batch: list[dict] = []
    problems: list[str] = []
    samples: Counter = Counter()  # 종류별로 몇 건을 이미 남겼는가
    seen_problems: set[str] = set()

    def flush() -> None:
        nonlocal written, pruned
        if not batch:
            return
        # 배치는 문서 경계에서만 끊기므로(아래) 여기 있는 문서는 조각이 다 모여 있다.
        tails: dict[str, int] = defaultdict(int)
        for row in batch:
            tails[row["doc_id"]] = max(tails[row["doc_id"]], int(row["ordinal"]) + 1)
        with conn.cursor() as cur:
            cur.executemany(UPSERT, batch)
            written += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
            cur.executemany(PRUNE, [{"doc_id": doc, "ordinal": tail} for doc, tail in tails.items()])
            pruned += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        conn.commit()
        batch.clear()

    def validate_and_flush() -> None:
        nonlocal over_target, over_target_max
        found, _per_source, lengths, _docs = check_rows(batch)
        # check_rows 의 종류별 3건 상한은 배치 안에서만 걸린다 -- 실측 규모(381,950청크 = 382배치)
        # 에서 배치마다 리셋되면 한 종류가 천 줄을 넘겨 보고가 다시 읽을 수 없게 된다(#18 M12).
        for problem in found:
            kind = problem_kind(problem)
            # 배치마다 행 번호가 2 부터 다시 세어지므로 같은 메시지가 여러 배치에서 나온다. 집합으로
            # 거른다 -- 앞의 `p not in problems` 는 problems 가 길어질수록 배치마다 다시 훑었다.
            if problem in seen_problems or samples[kind] >= SAMPLES_PER_KIND:
                continue
            seen_problems.add(problem)
            samples[kind] += 1
            problems.append(problem)
        # 하드스톱(1000자, check_rows)은 problems 로만 올린다 -- 500 을 그대로 problems 에 얹으면
        # 우리 split_text 는 500 이하만 내놓으니 걸릴 일이 없지만, 외부 청크를 검사할 때는
        # 지금 종료 코드 0 인 실행이 1 로 바뀐다. 그건 이 이슈가 아니라 M11 이 명시적으로 남겨둔 경계다.
        for length in lengths:
            if length > MAX_CHARS:
                over_target += 1
                over_target_max = max(over_target_max, length)
        flush()

    for row in chunk_rows(documents):
        # 배치는 **문서 경계에서만** 끊는다. "ordinal 이 0 부터 연속"은 문서 전체에 걸린 성질이라
        # 한 문서를 두 배치로 자르면 뒤쪽이 ordinal 5 부터 시작하는 것으로 보여 거짓 위반이 난다
        # (실측: 자막 한 편이 최대 155조각이라 자막에서만 수십 건). 30만 행을 리스트로 물리지
        # 않으려고 배치를 쓰는 것이므로, 상한을 넘긴 뒤 다음 문서가 시작될 때 끊는다.
        if batch and len(batch) >= WRITE_BATCH and row["doc_id"] != batch[-1]["doc_id"]:
            validate_and_flush()
        seen_docs.add(row["doc_id"])
        total += 1
        batch.append(row)
    validate_and_flush()
    return ChunkOutcome(len(seen_docs), total, written, problems, pruned, over_target, over_target_max)


CACHE_DIR = Path("var/retrieval/bm25")


def chunk_census(conn: psycopg.Connection, sources: tuple[str, ...] | None) -> tuple[int, datetime | None]:
    """(청크 수, 최신 `chunked_at`). 코퍼스가 지금 어디까지 와 있는지를 재는 한 자리다.

    BM25 캐시 키(index_signature)와 벡터 커버리지 가드(coverage_note)가 **같은 질의**를 봐야
    한다 -- 둘이 갈리면 한쪽은 따라가고 다른 쪽은 못 따라가는 지금의 어긋남이 다시 생긴다.
    """
    where, params = "", ()
    if sources:
        where, params = "WHERE source = ANY(%s)", (list(sources),)
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*), max(chunked_at) FROM retrieval_chunk {where}", params)  # noqa: S608
        count, latest = cur.fetchone() or (0, None)
    conn.commit()  # 뒤이어 형태소 분석이나 1.2GB 행렬 읽기가 붙는다 -- 트랜잭션을 열어 둔 채로 나가지 않는다
    return int(count or 0), latest


def _manifest_moment(value: object) -> datetime | None:
    """매니페스트의 ISO 문자열을 DB 의 timestamptz 옆에 놓는다. 못 읽으면 None -- 읽을 수 없는
    값은 어긋난 값이고, 어긋남은 아래에서 경고가 된다."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def coverage_note(conn: psycopg.Connection, store) -> str | None:
    """벡터 저장소가 지금 청크 집합을 덮는가. 덮으면 None, 아니면 사람이 읽을 한 줄.

    **멈추지 않는다.** 옛 코퍼스를 일부러 검색하는 것도 정상 용법이라 거부하면 그 길까지 막힌다 --
    막아야 하는 것은 조용한 것뿐이다. 어긋남을 고치는 것은 전량 재인코딩이지 이 함수가 아니다.

    대조 범위는 **저장소가 태운 소스**(매니페스트 `sources`)다. 검색의 `--source` 좁힘으로 재면
    좁힘 밖의 청크가 매번 "안 덮인다"로 나온다.

    `chunked_at_max` 는 필수 키가 아니다(vectors.REQUIRED_MANIFEST). 이 키가 생기기 전에 구운
    저장소를 거부하면 지금 도는 vector·hybrid 검색이 통째로 멈추므로, 없으면 개수만 대조하고
    그 사실을 말한다 -- 이 이슈가 정한 "멈추지 말고 알려라"와 같은 자리다.
    """
    scope = tuple(store.manifest.get("sources") or ()) or None
    count, latest = chunk_census(conn, scope)
    covered = len(store.chunk_ids)
    drift: list[str] = []
    if covered < count:
        drift.append(
            f"청크 {count:,}건 중 {covered:,}건만 벡터에 있다 -- "
            f"새 청크 {count - covered:,}건은 벡터 검색에 안 나온다"
        )
    elif covered > count:
        drift.append(f"벡터 {covered:,}건이 청크 {count:,}건보다 많다 -- 지워진 청크가 저장소에 남아 있다")
    blind = ""
    if "chunked_at_max" not in store.manifest:
        blind = (
            "매니페스트에 chunked_at_max 가 없어 개수만 대조했다 -- 같은 수 다른 집합은 다시 태워야 잡힌다"
        )
    elif (recorded := _manifest_moment(store.manifest["chunked_at_max"])) != latest:
        drift.append(
            f"청크는 {latest} 까지 바뀌었는데 벡터는 {recorded} 까지다 -- 수가 같아도 같은 집합이 아니다"
        )
    notes = drift + ([blind] if blind else [])
    if not notes:
        return None
    fix = " `cosmai retrieval embed` 로 전량 다시 태워야 맞는다." if drift else ""
    return "경고: " + "; ".join(notes) + "." + fix


def index_signature(conn: psycopg.Connection, sources: tuple[str, ...] | None) -> str:
    """이 색인이 무엇 위에 세워졌는지. 하나라도 달라지면 캐시를 다시 만들어야 한다.

    청크 수와 최신 `chunked_at` 이면 충분하다 -- UPSERT 가 본문이 바뀐 행만 `chunked_at` 을
    올리므로 내용 변화는 최댓값을 움직이고, 삭제는 개수를 움직인다. 토큰을 정하는 입력(사전 두 벌과
    주제 사전 topics.py)이 바뀌면 같은 본문이 다른 토큰이 되므로 그 해시도 넣는다(ydc bm25.py 의
    캐시 키와 같은 발상).
    """
    count, latest = chunk_census(conn, sources)
    parts = [str(count), str(latest), ",".join(sources or ())]
    for path in TOKENIZER_INPUTS:
        parts.append(hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "-")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def load_index(
    conn: psycopg.Connection,
    sources: tuple[str, ...] | None = None,
    *,
    cache_dir: Path | None = CACHE_DIR,
) -> tuple[Index, dict[str, str]]:
    """(색인, chunk_id -> source). 청크 단위로 색인한다 -- 문서 단위로 합치면 500자 제한이
    무의미해지고, 평가가 문서 단위를 원할 때는 `#ordinal` 을 떼어 접는다.

    **캐시가 없으면 쓸 수 없다.** 실측(2026-08-25, 381,950청크)으로 형태소 분석이 10분을 넘겨
    `cosmai retrieval search` 한 번이 그만큼 걸렸다. 피클에 담는 것은 클래스가 아니라 `state()`
    dict 다 -- 클래스를 담으면 모듈 경로가 바뀌는 날 캐시 전체를 못 읽는다.
    """
    cached = cache_dir / f"index-{index_signature(conn, sources)}.pkl" if cache_dir else None
    if cached and cached.exists():
        state = pickle.loads(cached.read_bytes())
        return Index.from_state(state["index"]), state["origin"]

    where, params = "", ()
    if sources:
        where, params = "WHERE source = ANY(%s)", (list(sources),)
    with conn.cursor() as cur:
        cur.execute(f"SELECT chunk_id, source, text FROM retrieval_chunk {where} ORDER BY chunk_id", params)  # noqa: S608
        rows = cur.fetchall()
    # 색인을 세우는 동안(38만 청크면 10분을 넘는다) 트랜잭션이 열려 있으면 needs_runtime 의
    # idle_in_transaction_session_timeout(15초)이 연결을 끊는다. 실측으로 여기서 끊겼다.
    conn.commit()
    ids = [r[0] for r in rows]
    index = Index(ids, [r[2] for r in rows])
    origin = {r[0]: r[1] for r in rows}
    if cached:
        cached.parent.mkdir(parents=True, exist_ok=True)
        # 임시 파일에 쓰고 옮긴다 -- 두 실행이 겹치면 반쯤 쓰인 피클을 읽게 된다.
        scratch = cached.with_suffix(f".{os.getpid()}.tmp")
        scratch.write_bytes(pickle.dumps({"index": index.state(), "origin": origin}))
        scratch.replace(cached)
    return index, origin


def ranked_chunks(
    conn: psycopg.Connection,
    query: str,
    *,
    engine: str = "bm25",
    top: int = 10,
    sources: tuple[str, ...] | None = None,
    store: Path | None = None,
    cache_dir: Path | None = CACHE_DIR,
    index: Index | None = None,
    vector_store=None,
    encoder=None,
) -> list[tuple[str, float]]:
    """(chunk_id, 점수). 세 검색기가 같은 모양으로 답한다 -- eval 이 같은 잣대로 재려면 필요하다.

    `index` · `vector_store` · `encoder` 를 넘기면 그것을 쓴다. eval 은 질의 61개를 연달아 돌리는데
    매번 다시 읽으면 96MB 피클과 1.2GB 행렬과 모델을 61번씩 여는 셈이다.

    점수의 뜻은 엔진마다 다르다(BM25 는 클수록, 벡터는 코사인 거리라 작을수록 가깝다). 그래서
    비교는 언제나 순위로 한다 -- RRF 를 쓰는 이유도 같다.
    """
    if engine == "bm25":
        lexical_index = index or load_index(conn, sources, cache_dir=cache_dir)[0]
        return lexical_index.search(query, k=top)

    from analysis.retrieval import embed, vectors

    out = store or vectors.DEFAULT_STORE
    loaded = vector_store
    if loaded is None:
        loaded = vectors.load(out)  # 파일이 없으면 StoreMissing 으로 여기서 멈춘다
        # 저장소를 연 쪽이 커버리지를 묻는다 -- eval 은 한 저장소로 질의 61개를 도므로 자기가 한 번
        # 묻고 그 답을 채점표에 싣는다(여기서 물으면 61번 세고 같은 줄을 61번 찍는다).
        if note := coverage_note(conn, loaded):
            print(note, file=sys.stderr)
    query_vector = embed.encode_query(query, out=out, store=loaded, encoder=encoder)
    if engine == "vector":
        return vectors.search(loaded, query_vector, top=top, sources=sources)
    if engine == "hybrid":
        lexical_index = index or load_index(conn, sources, cache_dir=cache_dir)[0]
        lexical = [c for c, _ in lexical_index.search(query, k=top * 4)]
        semantic = [c for c, _ in vectors.search(loaded, query_vector, top=top * 4, sources=sources)]
        fused = vectors.rrf(lexical, semantic)[:top]
        # 융합 결과의 점수는 순위 자체다. 두 스케일을 섞어 적으면 읽는 쪽이 오해한다.
        return [(chunk_id, float(rank)) for rank, chunk_id in enumerate(fused, 1)]
    raise ValueError(f"모르는 엔진: {engine!r}")


def search(
    conn: psycopg.Connection,
    query: str,
    *,
    engine: str = "bm25",
    top: int = 10,
    sources: tuple[str, ...] | None = None,
    store: Path | None = None,
    cache_dir: Path | None = CACHE_DIR,
) -> list[tuple[str, float, str]]:
    """(chunk_id, 점수, 본문)."""
    hits = ranked_chunks(
        conn, query, engine=engine, top=top, sources=sources, store=store, cache_dir=cache_dir
    )
    if not hits:
        return []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT chunk_id, text FROM retrieval_chunk WHERE chunk_id = ANY(%s)",
            ([chunk_id for chunk_id, _ in hits],),
        )
        texts = dict(cur.fetchall())
    # 여기서 커밋하지 않으면 부르는 쪽이 연결을 놓을 때까지 idle in transaction 으로 남는다 --
    # 그 트랜잭션은 vacuum 을 막고 needs_runtime 의 idle_in_transaction_session_timeout(15초)이
    # 끊을 때까지 산다(cosmai#58). 이 파일의 다른 SELECT 들은 이미 그렇게 하고 있다.
    conn.commit()
    return [(chunk_id, score, texts.get(chunk_id, "")) for chunk_id, score in hits]
