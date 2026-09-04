"""Search evaluation (slices/ydc/retrieval_eval.py). The scorer that measures whether a searcher is good.

The answers come for free -- the topics `topics.match_topics` attaches to a chunk body are the (document,
topic) labels. Nobody has to label anything by hand. ydc dropped them into `common/mention.csv` in advance,
but here the chunks are in the DB, so they are built while reading -- a separate label file would set solid
while out of step with the chunks.

The two modes are different questions.

  literal   query = one topic alias, answer = every document that topic is attached to.
            **This is for fault detection.** The answers themselves were made by string matching, so BM25
            does well by construction. Doing badly here means the tokenization is broken (dictionary not
            applied, normalization mismatch).

  heldout   query = alias A, answer = documents of the same topic with **not one token of A** in them.
            **This is the real measurement.** BM25 coming out near 0 is structurally normal, and that 0 is
            the line the vectors have to beat (the adoption criterion of #28 step 4).

Caution -- touch a parameter because of these results and from then on this number is not performance. The
automatic labels are for choosing what to touch; the human golden set is used once, in the final report.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import psycopg

from analysis.retrieval import bm25, topics

K = 10
FIELDS = (
    "mode",
    "engine",
    "topic_id",
    "query",
    "gold_size",
    "retrieved",
    "p_at_k",
    "mrr",
    "hit",
    "note",
    "store",
    "dictionary",
)
MODES = ("literal", "heldout")
ENGINES = ("bm25", "vector", "hybrid")

# `cache_dir=None` has to mean "do not use a cache". Reading None as "the default" leaves no way to turn it
# off, and the tests then leave an index in the repo's var/retrieval/bm25 (which really happened on
# 2026-08-25). So a separate marker stands for "not passed".
_DEFAULT_CACHE = Path("<default>")

GOLD_PAGE = 2000  # chunks fetched at once while building the answers (the same scale as corpus.BATCH)
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
    note: str = ""  # which corpus this score came out on. The same value per query, so any CSV line reads it
    store: str = ""  # which vector store it was measured with. Carried even when in step -- a different axis
    # from note (#49). Which topic dictionary the value stands on. **Always filled, whatever the engine** --
    # the dictionary makes both the answers and the queries, so even a bm25 row that opens no store stands on
    # the dictionary (#62).
    dictionary: str = ""


def queries(mode: str, dictionary: topics.Topics | None = None) -> list[tuple[str, str]]:
    """(topic_id, query). A query is a topic alias -- nobody makes the labels by hand."""
    out = []
    for entry in (dictionary or topics.active()).entries:
        if not entry["trend_use"]:
            continue  # a topic not used for the decision is left out of the evaluation as well
        aliases = entry["ko"] + entry["latin"]
        if mode == "heldout" and len(aliases) < 2:
            continue  # 별칭이 하나면 뺄 게 없다 (혼합자차)
        for alias in aliases:
            out.append((entry["topic"], alias))
    return out


def gold_from_chunks(
    conn: psycopg.Connection,
    sources: tuple[str, ...] | None = None,
    *,
    dictionary: topics.Topics | None = None,
) -> dict[str, set[str]]:
    """topic_id -> set of doc_id. Built by running match_topics over the chunk bodies.

    Folded per document -- with chunk_id answers, the number of pieces of one document decides the score.

    `sources` is narrowed to the same board as the indexing and the search -- a document outside the narrowed
    sources cannot come out of any engine, so left in the answers it cuts P@k · Hit@k and makes `gold_size`
    wrong.

    It walks one keyset page at a time and commits per page (the same way as `corpus._keyset`). Walking the
    rows in one stream with a server cursor holds that transaction open until the matching ends, and
    needs_runtime's `transaction_timeout` (60s, db/bootstrap.sql:48) is a cap on the **total lifetime** of a
    transaction and cuts it mid-way. That is also why the topic matching runs after the commit -- the slow
    side has to be outside the transaction.
    """
    # The dictionary that builds the answers is **the active version of this DB** -- built from a dictionary
    # left over in the process, nobody can say which dictionary that score came out on. If one was handed
    # over, that one is used. The queries and the rows have to write down **one and the same** revision for
    # the revision to be the revision of that score (#62).
    dictionary = dictionary or topics.use_active(conn)

    narrow, params = "", ()
    if sources:
        narrow, params = " AND source = ANY(%s)", (list(sources),)
    gold: dict[str, set[str]] = defaultdict(set)
    cursor = ""  # the chunk_id of the last row is the cursor of the next page
    while True:
        with conn.cursor() as cur:
            cur.execute(GOLD_SQL.format(source=narrow), (cursor, *params, GOLD_PAGE))  # noqa: S608
            rows = cur.fetchall()
        conn.commit()
        for _chunk_id, doc_id, text in rows:
            for topic in topics.match_topics(text, dictionary=dictionary):
                gold[topic].add(doc_id)
        if len(rows) < GOLD_PAGE:
            return gold
        cursor = rows[-1][0]


def docs_with_tokens(index: bm25.Index, query: str) -> set[str]:
    """Documents holding any query token. Taken out of the heldout answers.

    부분문자열로 빼면 안 된다 -- `하얘서` 를 Kiwi 는 `하얗` 으로 주므로 글자로는 안 겹치는데
    토큰으로는 겹친다. 색인이 실제로 쓰는 단위로 빼야 두 검색기가 같은 판에서 겨룬다.

    So this is `tokenize` rather than `tokenize_query` (#46). The answer definition is on the index axis, and
    riding the query stopwords this far leaves fewer documents to take out and widens the heldout answers --
    at which moment the .062 that grounded the vectors becomes a number on a different board.
    """
    found: set[str] = set()
    for term in set(bm25.tokenize(query)):
        for i, _tf in index.postings.get(term, ()):
            found.add(index.doc_ids[i].rsplit("#", 1)[0])
    return found


def to_docs(chunk_ids: list[str], k: int = K) -> list[str]:
    """Search results per document. Without this the shape differs from the answers (doc_id) and the score is
    always 0."""
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
    """(P@k, MRR@k, Hit@k). An empty answer set means that query has to be skipped, so it is blocked before
    the call."""
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
    """One row per query. The index is used for the heldout answers too, so it is always built whatever the
    engine."""
    if mode not in MODES:
        raise ValueError(f"mode 는 {MODES} 중 하나다: {mode!r}")
    if engine not in ENGINES:
        raise ValueError(f"engine 은 {ENGINES} 중 하나다: {engine!r}")

    # The index is used for the heldout answers too (taking out the documents holding a query token), so
    # whatever the engine, the answer definition has to be lexical for the three searchers to compete on the
    # same board.
    from analysis.retrieval.pipeline import coverage_note, load_index, ranked_chunks

    index, _ = load_index(conn, sources, cache_dir=_cache(cache_dir))
    # One dictionary is set up here and the answers, the queries and the rows' revision all look at it.
    # Reading the active dictionary separately would leave the three answers standing on different
    # dictionaries the day an activate lands mid-run (#62).
    dictionary = topics.use_active(conn)
    gold_all = gold_from_chunks(conn, sources, dictionary=dictionary)

    # The vector store and the model are opened once here. Opening them per query means opening a 1.2GB
    # matrix and the model 61 times.
    vector_store = encoder = None
    coverage = stamp = ""
    if engine != "bm25":
        from analysis.retrieval import embed, vectors

        vector_store = vectors.load(store or vectors.DEFAULT_STORE)
        # Whoever opened the store asks for the revision and the coverage together. It has to be on the score
        # sheet for "which corpus is this score on" to be readable -- a mismatch still gives a perfectly
        # normal-looking score, so the numbers alone cannot tell.
        coverage = coverage_note(conn, vector_store) or ""
        # The revision is on a different axis from the mismatch: put on the warning, nothing would be left
        # saying which store it was measured with **when everything is normal** (#49).
        stamp = vector_store.stamp
        encoder = embed.load_encoder(vector_store.model)

    rows: list[Row] = []
    for topic_id, query in queries(mode, dictionary):
        gold = set(gold_all.get(topic_id, ()))
        skip: set[str] = set()
        if mode == "heldout":
            skip = docs_with_tokens(index, query)
            gold -= skip
        if not gold:
            continue  # a query with no answers has no defined score
        # The candidates are not reduced. Two searchers have to compete on the same candidates and the same
        # answers for the scores to be comparable.
        hits = ranked_chunks(
            conn,
            query,
            engine=engine,
            top=k * 4,
            sources=sources,
            store=store,
            cache_dir=_cache(cache_dir),
            index=index,  # hand over what was built above -- rereading per query unpickles it 61 times
            vector_store=vector_store,
            encoder=encoder,
        )
        ranked = to_docs([c for c, _ in hits], k)
        p, mrr, hit = score(ranked, gold)
        rows.append(
            Row(
                mode,
                engine,
                topic_id,
                query,
                len(gold),
                len(ranked),
                p,
                mrr,
                hit,
                coverage,
                stamp,
                dictionary.stamp,
            )
        )
    return rows


def summary(rows: list[Row]) -> str:
    """A literal below 0.9 casts doubt on the tokenization -- this is fault detection, not a performance
    report."""
    if not rows:
        return "질의 0개 (청크가 비었는지 확인)"
    n = len(rows)
    p = sum(r.p_at_k for r in rows) / n
    mrr = sum(r.mrr for r in rows) / n
    hit = sum(1 for r in rows if r.hit) / n
    lines = [f"질의 {n}개 · P@{K} {p:.3f} · MRR@{K} {mrr:.3f} · Hit@{K} {hit:.0%}"]
    # The revision and the coverage warning have to show without opening the CSV -- someone who reads only
    # the summary copies it into the table.
    if stamp := next((r.store for r in rows if r.store), ""):
        lines.append(f"저장소 {stamp}")
    # The dictionary does not pick engines -- a bm25 summary needs this line too for that score to say which
    # dictionary it stands on.
    if known := next((r.dictionary for r in rows if r.dictionary), ""):
        lines.append(f"사전 {known}")
    if note := next((r.note for r in rows if r.note), ""):
        lines.append(note)
    return "\n".join(lines)
