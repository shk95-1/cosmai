"""Source -> chunks -> needs.retrieval_chunk, and the BM25 search built on those chunks.

Committed per batch. needs_runtime's transaction_timeout is 60s, so 300k rows in one transaction do not make
it to the end -- analysis/aggregate and analysis/polarity take the same shape for the same reason.
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

# The doc_ids that still have chunks in the scanned sources are walked back with a keyset. Walking 380k
# chunks in one statement hits needs_runtime's statement_timeout (30s) -- the same reason and the same shape
# as the paging in corpus.py.
STORED_DOCS = """
SELECT DISTINCT doc_id FROM retrieval_chunk
WHERE source = ANY(%(sources)s) AND doc_id > %(cursor)s
ORDER BY doc_id LIMIT %(limit)s
"""

DROP_DOCS = "DELETE FROM retrieval_chunk WHERE doc_id = ANY(%(doc_ids)s)"

# A document is not deleted wholesale and reinserted -- deleted and reinserted, the search is empty in
# between. If the tail of a shortened document (an ordinal at or above the new piece count) is left, "ordinal
# is contiguous from 0" (contracts/ddl/needs/020_retrieval_chunk.sql:15) breaks at the table level -- and
# check_rows, which sees only a batch, saw that whole document again and reports no violation. It runs after
# the UPSERT in the same transaction.
#
# The evidence for all three deletions is **what this run saw inside the range it scanned** (#23). Shortened
# and emptied are facts known with the document in hand; vanished is not -- "it did not come up in the scan"
# is indistinguishable, in an incremental run (--since), from "it was out of range so it was not looked at",
# so that decision only stands in a full scan (_drop_vanished).


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
            # It means the source got shorter, which is not something to pass over quietly -- collectors only
            # add.
            head += f"; 짧아진 문서의 꼬리 {self.pruned:,} 삭제"
        if self.emptied:
            # "Got shorter" and "went empty entirely" are different events at the source, so they are counted
            # apart -- the latter means the document dropped out of the index, and that has to read as bigger
            # than a few tails.
            head += f"; 본문이 빈 문서의 청크 {self.emptied:,} 삭제"
        if self.vanished:
            head += f"; 원천에서 사라진 문서의 청크 {self.vanished:,} 삭제"
        if not self.swept:
            # Not doing it and passing over it quietly reads as "running --since daily also tidies up".
            head += "; 증분 범위라 사라진 문서는 찾지 않았다"
        if self.unscanned:
            head += f"; 문서 0건인 소스({', '.join(self.unscanned)})는 정리에서 제외"
        if self.over_target:
            # Under the hard stop (1000 chars) so it is not a problem, but "[pass]" once read as no 500
            # violations and buried 27 chunks of someone else's (ydc v0.2.0) -- how many there are always has
            # to show.
            head += f"; 목표 상한 초과 {self.over_target:,}건 (최대 {self.over_target_max:,}자)"
        if not self.problems:
            return head
        # problems is a few samples per kind, not a violation count -- calling its length "kinds" makes 3
        # samples of one kind read as "3 kinds" (#18 M12).
        kinds = len({problem_kind(p) for p in self.problems})
        return f"{head}; 계약 위반 {kinds}종"


def document_rows(documents: Iterable[corpus.Document]) -> Iterator[tuple[corpus.Document, list[dict]]]:
    """(document, the chunk rows of that document). **A document with 0 pieces is emitted too** -- swallowing
    a document whose body went entirely empty here leaves the caller unaware it was even scanned, and the old
    chunks stay forever (#23)."""
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
    """One document into zero or more chunk rows. An empty body does not go into the index."""
    for _document, rows in document_rows(documents):
        yield from rows


def _drop_vanished(conn: psycopg.Connection, sources: tuple[str, ...], kept: set[str]) -> int:
    """Deletes the chunks of the documents that did not come up in this scan among the doc_ids left in the
    scanned sources, and gives the number of rows deleted.

    **Called only after the caller has confirmed this is a full scan.** The only evidence for "it vanished"
    here is "it was scanned and did not come up", so called from an incremental scan (`--since`) it deletes
    out-of-range documents wholesale.
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
        # Committed per page -- 380k chunks in one transaction hit transaction_timeout (60s).
        conn.commit()
        if len(stored) < corpus.BATCH:
            return dropped
        # The cursor ascends by doc_id and what was deleted is always behind it, so a delete never makes the
        # next page skip.
        cursor = stored[-1]


def run(
    conn: psycopg.Connection,
    *,
    youtube_schema: str = "tubedepth",
    commerce_schema: str = "trend_radar",
    mfds_schema: str = "needs",
    since: date | None = None,
    sources: tuple[str, ...] = corpus.SOURCES,
) -> ChunkOutcome:
    """Scans the sources and loads the chunks. Contract violations are counted and returned but do not block
    the load -- one source's defect emptying the index of the other three is worse."""
    documents = corpus.documents(
        conn,
        youtube_schema=youtube_schema,
        commerce_schema=commerce_schema,
        mfds_schema=mfds_schema,
        since=since,
        sources=sources,
    )
    seen_docs: set[str] = set()
    scanned: Counter = Counter()  # documents seen per source in the scan
    total = written = pruned = emptied = over_target = over_target_max = checked = 0
    batch: list[dict] = []
    empty_docs: list[str] = []  # documents seen with an empty body. Deleted wholesale at the next flush
    problems: list[str] = []
    samples: Counter = Counter()  # how many of each kind have been kept already
    seen_problems: set[str] = set()

    def flush() -> None:
        nonlocal written, pruned, emptied
        if not batch and not empty_docs:
            return
        # A batch is cut only on a document boundary (below), so the documents here have all their pieces.
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
                # A document with 0 pieces, so the same statement as the tail delete removes the whole
                # document. Only the counting is kept apart -- it is the same transaction as the load, so
                # there is no moment at which the search sees an empty chunk.
                cur.executemany(PRUNE, [{"doc_id": d, "ordinal": 0} for d in empty_docs])
                emptied += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        conn.commit()
        batch.clear()
        empty_docs.clear()

    def validate_and_flush() -> None:
        nonlocal over_target, over_target_max, checked
        # The number attached to a row with no coordinate is counted across the whole run -- starting at 2
        # per batch, one coordinate would point at several documents, and the message telling a person to go
        # find the original would lose its purpose (#27).
        found, _per_source, lengths, _docs = check_rows(batch, first_line=checked + 2)
        checked += len(lengths)
        # The 3-per-kind cap of check_rows applies only inside a batch -- at the measured scale (381,950
        # chunks = 382 batches), reset per batch, one kind would run past a thousand lines and the report
        # would stop being readable (#18 M12).
        for problem in found:
            kind = problem_kind(problem)
            # Violations with the same coordinate say the same row twice, so a set filters them -- the
            # earlier `p not in problems` rescanned problems per batch as it grew.
            if problem in seen_problems or samples[kind] >= SAMPLES_PER_KIND:
                continue
            seen_problems.add(problem)
            samples[kind] += 1
            problems.append(problem)
        # The hard stop (1000 chars, check_rows) is raised as problems only -- putting 500 straight into
        # problems would never trigger for us since our split_text emits nothing over 500, but checking
        # external chunks it would turn a run that exits 0 today into a 1. That is not this issue but the
        # boundary M11 left explicitly.
        for length in lengths:
            if length > MAX_CHARS:
                over_target += 1
                over_target_max = max(over_target_max, length)
        flush()

    for document, rows in document_rows(documents):
        scanned[document.source] += 1
        # A batch is cut **only on a document boundary**. "ordinal is contiguous from 0" is a property of the
        # whole document, so cutting one document into two batches makes the second look like it starts at
        # ordinal 5 and raises a false violation (measured: one transcript runs to 155 pieces, so dozens of
        # them in transcripts alone). Batching exists so 300k rows are not held as a list, so the cut is made
        # when the next document starts after the cap is passed.
        if len(batch) >= WRITE_BATCH or len(empty_docs) >= WRITE_BATCH:
            validate_and_flush()
        if not rows:
            # The empty body was **seen directly in the scan**. Incremental or not, the evidence is inside
            # this run, so there is no reason to defer it -- deferred, chunks pointing at a source that is
            # gone keep coming up in the search (#23).
            empty_docs.append(document.doc_id)
            continue
        seen_docs.add(document.doc_id)
        total += len(rows)
        batch.extend(rows)
    validate_and_flush()

    # Looking for vanished documents only stands in a full scan. `--since` does not read out-of-range
    # documents at all, so "it did not come up" is no evidence that it vanished -- read that way, one
    # incremental run erases the corpus.
    swept = since is None
    unscanned = tuple(name for name in sources if not scanned[name]) if swept else ()
    vanished = 0
    if swept:
        # For a source scanned to 0 documents, "they all vanished" and "it could not be read" (an empty
        # schema, a collector that did not run) are indistinguishable. A delete cannot be undone, so that
        # source is taken out of range and note says it was.
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
    """(chunk count, newest `chunked_at`). The one place that measures how far the corpus has come.

    The BM25 cache key (index_signature) and the vector coverage guard (coverage_note) have to look at **the
    same query** -- split, the current mismatch where one follows and the other cannot comes back.
    """
    where, params = "", ()
    if sources:
        where, params = "WHERE source = ANY(%s)", (list(sources),)
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*), max(chunked_at) FROM retrieval_chunk {where}", params)  # noqa: S608
        count, latest = cur.fetchone() or (0, None)
    conn.commit()  # morphological analysis or a 1.2GB matrix read follows -- do not leave a transaction open
    return int(count or 0), latest


def _manifest_moment(value: object) -> datetime | None:
    """Puts the manifest's ISO string next to the DB's timestamptz. None when it cannot be read -- a value
    that cannot be read is a value out of step, and being out of step becomes a warning below."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def coverage_note(conn: psycopg.Connection, store) -> str | None:
    """Does the vector store cover the current chunk set. None if it does, otherwise one line for a person.

    **It does not stop.** Searching an old corpus on purpose is a normal use as well, so refusing would block
    that path too -- what has to be blocked is only the quiet case. What fixes a mismatch is a full re-encode,
    not this function.

    The comparison range is **the sources the store burned** (the manifest's `sources`). Measured against the
    search's `--source` narrowing, chunks outside the narrowing come out as "not covered" every time.

    `chunked_at_max` is not a required key (vectors.REQUIRED_MANIFEST). Refusing a store burned before that
    key existed would stop every vector and hybrid search running today, so when it is missing only the
    counts are compared and that fact is said -- the same place as the "do not stop, tell them" this issue
    settled.
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
    """What this index was built on. One difference and the cache has to be rebuilt.

    The chunk count and the newest `chunked_at` are enough -- the UPSERT raises `chunked_at` only on rows
    whose body changed, so a content change moves the maximum and a delete moves the count. When an input
    that decides the tokens (the two Kiwi dictionaries) changes, the same body becomes different tokens, so
    those hashes go in too (the same idea as the cache key in ydc bm25.py).

    **The topic dictionary is not a file.** The aliases are Kiwi user words and an expansion list, so they
    decide tokens, and since their source moved to the active version of `needs.aspect_lexicon`, hashing
    `topics.py` no longer covers the topic content (#8) -- so the active version number and the fingerprint
    of its content are bitten together. The number alone is not enough: rows can be added to a version that
    is already switched on, and then the number stays.
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
    """(index, chunk_id -> source). Indexed per chunk -- merged per document the 500-character limit would
    mean nothing, and when the evaluation wants documents the `#ordinal` is stripped and they are folded.

    **It cannot be used without the cache.** Measured (2026-08-25, 381,950 chunks), the morphological
    analysis ran over 10 minutes and one `cosmai retrieval search` took that long. What goes into the pickle
    is the `state()` dict rather than the class -- put the class in and the whole cache becomes unreadable
    the day the module path changes.
    """
    # The dictionary has to be set up even when the cache is not used -- the tokenization below it reads the
    # active dictionary.
    topics.use_active(conn)
    # The query stopwords are set up here too. They are not used for the index, but every query running on
    # this index goes below it and this is the one place that opens the DB (#46). Missing, it is an empty
    # list, so setting it up does not itself fail.
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
    # With the transaction open while the index is built (over 10 minutes at 380k chunks), needs_runtime's
    # idle_in_transaction_session_timeout (15s) cuts the connection. Measured, it was cut right here.
    conn.commit()
    ids = [r[0] for r in rows]
    index = Index(ids, [r[2] for r in rows])
    origin = {r[0]: r[1] for r in rows}
    if cached:
        cached.parent.mkdir(parents=True, exist_ok=True)
        # Written to a temporary file and moved -- two overlapping runs would read a half-written pickle.
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
    """(chunk_id, score). The three searchers answer in the same shape -- eval needs it to measure them by
    one yardstick.

    Passing `index` · `vector_store` · `encoder` uses those. eval runs 63 queries in a row, and rereading
    each time means opening a 96MB pickle, a 1.2GB matrix and the model 61 times each.

    The meaning of the score differs per engine (higher is closer for BM25; for vectors it is a cosine
    distance, so lower is closer). So comparison is always by rank -- the same reason RRF is used.
    """
    if engine == "bm25":
        lexical_index = index or load_index(conn, sources, cache_dir=cache_dir)[0]
        return lexical_index.search(query, k=top)

    from analysis.retrieval import embed, vectors

    out = store or vectors.DEFAULT_STORE
    loaded = vector_store
    if loaded is None:
        loaded = vectors.load(out)  # with no file it stops here with StoreMissing
        # Whoever opened the store asks for the coverage -- eval runs 63 queries on one store, so it asks
        # once itself and puts the answer on the score sheet (asking here would count 61 times and print the
        # same line 61 times).
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
        # The score of a fused result is the rank itself. Writing two mixed scales misleads the reader.
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
    index: Index | None = None,
    vector_store=None,
    encoder=None,
) -> list[tuple[str, float, str]]:
    """(chunk_id, score, body). `vector` and `hybrid` block a query with no grounding before ranking it.

    **It is not applied to `bm25`.** Lexical search ignores a word with df 0 by giving it idf 0 and
    **answers with the words that are left**, so blocking would turn the partial answer that used to come out
    of a "real topic + a new product name not yet in the corpus" query into 0 results. Nobody has measured
    that loss (there is no query log), and there is no reason to accept an unmeasured loss.

    **The index is opened whatever the engine.** bm25 and hybrid use it for ranking, and vector because of
    the df the gate looks at -- vector has never opened the index until now, so that is the cost this issue
    created. With a cache it is one pickle; without, it is the ten-odd minutes of morphological analysis over
    380k chunks, and **the cache is separate per `--source` combination** (`index_signature` bites
    `sources`). A host with no vector file sees `StoreMissing` (2) only **after** paying that cost -- because
    the gate comes before the store.
    """
    # The three handles are pass-throughs to ranked_chunks, which already takes them: a caller that
    # has to read the index or the store for something else (ask.py reads both for its version note)
    # would otherwise open them a second time here. Behaviour is unchanged when they are None.
    index = index or load_index(conn, sources, cache_dir=cache_dir)[0]
    if engine in ("vector", "hybrid") and not (grounded := grounding.check(query, index)).ok:
        # 0 results is an answer the contract already knows (exit code 1) -- no new code is added, only the
        # reason is said.
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
        index=index,  # hand over what the gate opened -- bm25 and hybrid do not open the same index twice
        vector_store=vector_store,
        encoder=encoder,
    )
    # A vector encodes the raw query instead of tokenizing it, so it does not ride this list -- printing this
    # line there would state something that did not happen.
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
    # Without a commit here it stays idle in transaction until the caller drops the connection -- that
    # transaction blocks vacuum and lives until needs_runtime's idle_in_transaction_session_timeout (15s)
    # cuts it (cosmai#58). The other SELECTs in this file already do this.
    conn.commit()
    return [(chunk_id, score, texts.get(chunk_id, "")) for chunk_id, score in hits]
