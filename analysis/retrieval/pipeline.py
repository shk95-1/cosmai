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

from analysis.retrieval import corpus, grounding, stopwords, topics
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

# 훑은 소스에 청크가 남아 있는 doc_id 를 키셋으로 되짚는다. 38만 청크를 한 문장으로 훑으면
# needs_runtime 의 statement_timeout(30초)에 걸린다 -- corpus.py 의 페이징과 같은 이유, 같은 모양이다.
STORED_DOCS = """
SELECT DISTINCT doc_id FROM retrieval_chunk
WHERE source = ANY(%(sources)s) AND doc_id > %(cursor)s
ORDER BY doc_id LIMIT %(limit)s
"""

DROP_DOCS = "DELETE FROM retrieval_chunk WHERE doc_id = ANY(%(doc_ids)s)"

# 문서를 통째로 지우고 다시 넣지는 않는다 -- 지웠다 넣으면 그 사이 검색이 빈다. 짧아진 문서의
# 꼬리(새 조각 수 이상의 ordinal)가 남으면 "ordinal 은 0 부터 연속"
# (contracts/ddl/needs/020_retrieval_chunk.sql:15)이 표 수준에서 깨진다 -- 배치만 보는 check_rows 는
# 그 문서를 다시 다 봤으므로 위반을 못 낸다. 같은 트랜잭션에서 UPSERT 뒤에 돈다.
#
# 지우는 근거는 셋 다 **이번 실행이 훑은 범위 안에서 본 것**이다(#23). 짧아졌다·본문이 비었다는
# 문서를 손에 들고 아는 사실이고, 사라졌다는 그렇지 않다 -- "훑기에 안 나왔다"는 증분 실행(--since)
# 에서 "범위 밖이라 안 봤다"와 구분되지 않으므로, 그 판정은 전량 훑기에서만 선다(_drop_vanished).


@dataclass(frozen=True)
class ChunkOutcome:
    documents: int
    chunks: int
    written: int
    problems: list[str]
    pruned: int = 0
    over_target: int = 0
    over_target_max: int = 0
    emptied: int = 0
    vanished: int = 0
    swept: bool = True
    unscanned: tuple[str, ...] = ()

    @property
    def note(self) -> str:
        head = f"문서 {self.documents:,} -> 청크 {self.chunks:,} (변경 {self.written:,})"
        if self.pruned:
            # 원천이 짧아졌다는 뜻이라 조용히 넘어갈 일이 아니다 -- 수집기는 추가만 한다.
            head += f"; 짧아진 문서의 꼬리 {self.pruned:,} 삭제"
        if self.emptied:
            # "짧아졌다"와 "통째로 비었다"는 원천에서 다른 일이라 세는 자리를 나눈다 -- 후자는
            # 문서가 색인에서 빠졌다는 뜻이고, 그건 꼬리 몇 개보다 크게 읽혀야 한다.
            head += f"; 본문이 빈 문서의 청크 {self.emptied:,} 삭제"
        if self.vanished:
            head += f"; 원천에서 사라진 문서의 청크 {self.vanished:,} 삭제"
        if not self.swept:
            # 안 한 일이라 조용히 넘어가면 "매일 --since 로 돌리니 정리도 된다"로 읽힌다.
            head += "; 증분 범위라 사라진 문서는 찾지 않았다"
        if self.unscanned:
            head += f"; 문서 0건인 소스({', '.join(self.unscanned)})는 정리에서 제외"
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


def document_rows(documents: Iterable[corpus.Document]) -> Iterator[tuple[corpus.Document, list[dict]]]:
    """(문서, 그 문서의 청크 행). **조각이 0개인 문서도 낸다** -- 본문이 통째로 빈 문서를 여기서
    삼키면 부르는 쪽은 그 문서를 훑었다는 사실조차 모르고, 옛 청크가 영구히 남는다(#23)."""
    for document in documents:
        pieces = split_text(normalize_text(document.text))
        yield (
            document,
            [
                {
                    "chunk_id": f"{document.doc_id}#{ordinal}",
                    "doc_id": document.doc_id,
                    "source": document.source,
                    "ordinal": ordinal,
                    "text": piece,
                    "text_md5": hashlib.md5(piece.encode()).hexdigest(),
                }
                for ordinal, piece in enumerate(pieces)
            ],
        )


def chunk_rows(documents: Iterable[corpus.Document]) -> Iterator[dict]:
    """문서 하나를 0개 이상의 청크 행으로. 빈 본문은 색인에 넣지 않는다."""
    for _document, rows in document_rows(documents):
        yield from rows


def _drop_vanished(conn: psycopg.Connection, sources: tuple[str, ...], kept: set[str]) -> int:
    """훑은 소스에 남은 doc_id 중 이번 훑기에 안 나온 문서의 청크를 지우고, 지운 행 수를 준다.

    **부르는 쪽이 전량 훑기임을 확인한 뒤에만 부른다.** 여기서 "사라졌다"의 근거는 "훑었는데
    안 나왔다" 하나뿐이라, 증분 훑기(`--since`)에서 부르면 범위 밖 문서를 통째로 지운다.
    """
    dropped, cursor = 0, ""
    while True:
        with conn.cursor() as cur:
            cur.execute(STORED_DOCS, {"sources": list(sources), "cursor": cursor, "limit": corpus.BATCH})
            stored = [row[0] for row in cur.fetchall()]
            gone = [doc_id for doc_id in stored if doc_id not in kept]
            if gone:
                cur.execute(DROP_DOCS, {"doc_ids": gone})
                dropped += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        # 페이지마다 커밋한다 -- 38만 청크를 한 트랜잭션에 담으면 transaction_timeout(60초)에 걸린다.
        conn.commit()
        if len(stored) < corpus.BATCH:
            return dropped
        # 커서는 doc_id 오름차순이고 지운 것은 언제나 커서 앞이라, 삭제가 다음 페이지를 건너뛰지 않는다.
        cursor = stored[-1]


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
    scanned: Counter = Counter()  # 소스별로 훑어서 본 문서 수
    total = written = pruned = emptied = over_target = over_target_max = checked = 0
    batch: list[dict] = []
    empty_docs: list[str] = []  # 훑어서 본문이 빈 것을 본 문서. 다음 flush 에서 통째로 지운다
    problems: list[str] = []
    samples: Counter = Counter()  # 종류별로 몇 건을 이미 남겼는가
    seen_problems: set[str] = set()

    def flush() -> None:
        nonlocal written, pruned, emptied
        if not batch and not empty_docs:
            return
        # 배치는 문서 경계에서만 끊기므로(아래) 여기 있는 문서는 조각이 다 모여 있다.
        tails: dict[str, int] = defaultdict(int)
        for row in batch:
            tails[row["doc_id"]] = max(tails[row["doc_id"]], int(row["ordinal"]) + 1)
        with conn.cursor() as cur:
            if batch:
                cur.executemany(UPSERT, batch)
                written += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
                cur.executemany(PRUNE, [{"doc_id": d, "ordinal": t} for d, t in tails.items()])
                pruned += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
            if empty_docs:
                # 조각이 0개인 문서라 꼬리 삭제와 같은 문장으로 문서 전체가 지워진다. 다만 세는
                # 자리는 나눈다 -- 적재와 같은 트랜잭션이라 검색이 빈 청크를 보는 순간이 없다.
                cur.executemany(PRUNE, [{"doc_id": d, "ordinal": 0} for d in empty_docs])
                emptied += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        conn.commit()
        batch.clear()
        empty_docs.clear()

    def validate_and_flush() -> None:
        nonlocal over_target, over_target_max, checked
        # 좌표 없는 행에 붙는 번호는 실행 전체로 이어 센다 -- 배치마다 2 부터면 한 좌표가 여러
        # 문서를 가리키고, 그러면 사람이 원본을 찾아가라는 메시지의 목적이 없어진다(#27).
        found, _per_source, lengths, _docs = check_rows(batch, first_line=checked + 2)
        checked += len(lengths)
        # check_rows 의 종류별 3건 상한은 배치 안에서만 걸린다 -- 실측 규모(381,950청크 = 382배치)
        # 에서 배치마다 리셋되면 한 종류가 천 줄을 넘겨 보고가 다시 읽을 수 없게 된다(#18 M12).
        for problem in found:
            kind = problem_kind(problem)
            # 좌표가 같은 위반은 같은 행을 두 번 말하는 것이라 집합으로 거른다 -- 앞의
            # `p not in problems` 는 problems 가 길어질수록 배치마다 다시 훑었다.
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

    for document, rows in document_rows(documents):
        scanned[document.source] += 1
        # 배치는 **문서 경계에서만** 끊는다. "ordinal 이 0 부터 연속"은 문서 전체에 걸린 성질이라
        # 한 문서를 두 배치로 자르면 뒤쪽이 ordinal 5 부터 시작하는 것으로 보여 거짓 위반이 난다
        # (실측: 자막 한 편이 최대 155조각이라 자막에서만 수십 건). 30만 행을 리스트로 물리지
        # 않으려고 배치를 쓰는 것이므로, 상한을 넘긴 뒤 다음 문서가 시작될 때 끊는다.
        if len(batch) >= WRITE_BATCH or len(empty_docs) >= WRITE_BATCH:
            validate_and_flush()
        if not rows:
            # 본문이 빈 것을 **훑어서 직접 봤다**. 증분이든 아니든 근거가 이 실행 안에 있으므로
            # 미룰 이유가 없다 -- 미루면 사라진 원천을 가리키는 청크가 계속 검색에 잡힌다(#23).
            empty_docs.append(document.doc_id)
            continue
        seen_docs.add(document.doc_id)
        total += len(rows)
        batch.extend(rows)
    validate_and_flush()

    # 사라진 문서 찾기는 전량 훑기에서만 선다. `--since` 는 범위 밖 문서를 아예 읽지 않으므로
    # "안 나왔다"가 사라졌다는 근거가 되지 못한다 -- 그렇게 읽으면 증분 실행 한 번이 코퍼스를 지운다.
    swept = since is None
    unscanned = tuple(name for name in sources if not scanned[name]) if swept else ()
    vanished = 0
    if swept:
        # 훑어서 문서가 0건인 소스는 "다 사라졌다"와 "못 읽었다"(빈 스키마·안 돈 수집기)가 구분되지
        # 않는다. 삭제는 되돌릴 수 없으므로 그 소스는 범위에서 빼고, 뺐다는 사실을 note 가 말한다.
        scope = tuple(name for name in sources if scanned[name])
        vanished = _drop_vanished(conn, scope, seen_docs) if scope else 0
    return ChunkOutcome(
        len(seen_docs),
        total,
        written,
        problems,
        pruned,
        over_target,
        over_target_max,
        emptied=emptied,
        vanished=vanished,
        swept=swept,
        unscanned=unscanned,
    )


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
    올리므로 내용 변화는 최댓값을 움직이고, 삭제는 개수를 움직인다. 토큰을 정하는 입력(Kiwi 사전
    두 벌)이 바뀌면 같은 본문이 다른 토큰이 되므로 그 해시도 넣는다(ydc bm25.py 의 캐시 키와 같은
    발상).

    **주제 사전은 파일이 아니다.** 별칭은 Kiwi 사용자 단어이자 확장 목록이라 토큰을 정하는데,
    그 원천이 `needs.aspect_lexicon` 의 활성 버전으로 옮겨간 뒤로는 `topics.py` 를 해시해도 주제
    내용을 덮지 못한다(#8) -- 그래서 활성 버전 번호와 그 내용 지문을 함께 문다. 번호만으로는
    모자란다: 이미 켜져 있는 버전에 행을 더 넣을 수 있고, 그러면 번호는 그대로다.
    """
    count, latest = chunk_census(conn, sources)
    dictionary = topics.use_active(conn)
    parts = [
        str(count),
        str(latest),
        ",".join(sources or ()),
        f"topics:v{dictionary.version}:{dictionary.fingerprint}",
    ]
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
    # 캐시를 안 쓸 때도 사전은 세워야 한다 -- 토큰화가 그 아래에서 활성 사전을 읽는다.
    topics.use_active(conn)
    # 질의 불용어도 여기서 세운다. 색인에는 안 쓰이지만 이 색인으로 도는 질의가 전부 그 아래를 타고,
    # DB 를 여는 자리는 여기 하나다 (#46). 없으면 빈 목록이라 세우는 것 자체가 실패하지 않는다.
    stopwords.use_active(conn)
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
    """(chunk_id, 점수, 본문). 근거 없는 질의는 순위를 매기기 전에 막는다.

    **색인은 엔진과 무관하게 연다.** 게이트가 보는 df 가 거기 있어서인데, `--engine vector` 는 지금까지
    색인을 안 열었으므로 이 자리가 비용이다 -- 캐시가 있으면 피클 한 벌이고, 없으면 38만 청크를 형태소
    분석하는 십수 분이다(`load_index`). 그 비용을 내는 이유는 코사인 하한선이 못 쓰는 것으로 판정났기
    때문이고(계약 §벡터 하한선), 코퍼스에 없는 이름을 물었을 때 상위 k 가 근거로 인쇄되는 것보다는 낫다.
    """
    index, _ = load_index(conn, sources, cache_dir=cache_dir)
    if not (grounded := grounding.check(query, index)).ok:
        # 결과 0건은 이미 계약이 아는 답이다(종료 코드 1) -- 새 코드를 늘리지 않고 이유만 말한다.
        print(grounded.note, file=sys.stderr)
        return []
    hits = ranked_chunks(
        conn,
        query,
        engine=engine,
        top=top,
        sources=sources,
        store=store,
        cache_dir=cache_dir,
        index=index,  # 게이트가 이미 연 것을 넘긴다 -- bm25·hybrid 가 같은 색인을 두 번 열지 않는다
    )
    # 벡터는 질의를 토큰화하지 않고 원문을 인코딩하므로 이 목록을 안 탄다 -- 그쪽에 이 줄을 찍으면
    # 안 일어난 일을 말하게 된다.
    if engine != "vector" and (note := stopwords.query_note(query)):
        print(note, file=sys.stderr)
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
