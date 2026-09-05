"""The chunk index + the commerce source + the judgement of that run -> the three cross-source answers
(fork #7).

**This pipeline writes nothing.** A row of the three answers is keyed by one (topic) or one (ingredient),
while 022's quarterly grain is keyed by eight columns and the commerce side has neither the quarter nor the
roster among them (`contracts/interfaces.md` §Crosscheck). So the output is an answer rather than a table,
and being read-only it is run against the production DB as it is.

The chunk index is scanned once. Scanned in one stream, the transaction stays open until the matching ends,
and `needs_runtime`'s `transaction_timeout` (60 seconds) is a cap on the **total lifetime** of a transaction
and cuts it mid-way -- so pages are taken one keyset page at a time and committed per page (the same way and
for the same reason as `gold_from_chunks` in `analysis/retrieval/eval.py`). It was really measured over all
381,950 chunks: 48MB in 11.3 seconds.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any, LiteralString

import psycopg
from psycopg import sql as pgsql

from analysis import crosscheck
from analysis.crosscheck import IngredientRow, Ingredients, RatingRow, SourceShare
from analysis.retrieval import corpus
from analysis.retrieval import topics as topic_registry
from analysis.trend.pipeline import COMMENT, CONTENT_TYPE, PANEL_ROLE, SCOPE, NoPopulation, note_of
from db.corpus import active_snapshot
from db.seed import panel as panel_seed

COMMERCE_SCHEMA = "trend_radar"
# 커머스 쪽 선케어 모집단은 **랭킹이 정한다.** 이름 부분문자열로 고르는 것은 §성분 의 `시카` 사고와 같은
# 실수다 -- 짧은 별칭이 다른 것을 잡는다. 여기서는 플랫폼이 선케어 보드·카테고리에 올린 제품이 답이다.
SUN_BOARD = "suncare"
SUN_CATEGORY = ("%선케어%", "%선크림%", "%선블록%", "%선스틱%", "%선쿠션%")
CHUNK_PAGE = 20_000

SUN_JOIN = """
  JOIN (SELECT DISTINCT source, product_key FROM {rank}
         WHERE board = %(board)s OR category_name ILIKE ANY(%(category)s)) sun
    USING (source, product_key)
"""
SUN_REVIEWS = "SELECT r.source, r.review_key FROM {review} r" + SUN_JOIN
RATED = (
    "SELECT t.source, t.product_key, t.topic_group, t.topic_name, t.share_pct, t.captured_at "
    # A source whose `share_pct` is NULL carries the weight (`score`) instead of the share. A weight and a
    # percentage are different units, so mixing and averaging them shows nothing and is wrong (the DDL
    # comment of `review_topic.score`).
    "FROM {topic} t" + SUN_JOIN + " WHERE t.share_pct IS NOT NULL"
)
FORMULA = "SELECT source, product_key, ingredients FROM {product} WHERE coalesce(ingredients, '') <> ''"
SUN_PRODUCTS = (
    "SELECT count(*) FROM (SELECT DISTINCT source, product_key FROM {rank} "
    "WHERE board = %(board)s OR category_name ILIKE ANY(%(category)s)) sun"
)

CHUNKS: LiteralString = (
    "SELECT chunk_id, source, doc_id, text FROM retrieval_chunk "
    "WHERE chunk_id > %s ORDER BY chunk_id LIMIT %s"
)
CHUNK_DOCS: LiteralString = "SELECT source, count(DISTINCT doc_id) FROM retrieval_chunk GROUP BY 1"
FIND_RUN: LiteralString = "SELECT run_id FROM analysis_run WHERE note = %s ORDER BY run_id LIMIT 1"
# The judgement and the metrics in one. The rank and the share are not in the judgement table but in the
# metrics table (024 holds no counted column). Six of the eight key columns are bound (`quarter` and
# `topic_key` are the axes of the answer and are left free). Leaving `content_type` and `panel_role` open
# makes it **non-deterministic which judgement wins** in the topic-keyed dict of `_judged` the day a second
# value appears -- it simply does not blow up today because a run is only (long_form, product) (#43 is the
# same family).
CELLS: LiteralString = """
SELECT j.quarter, j.topic_key, j.trend_type, j.gap_pp, m.composition
  FROM topic_quarter_judgement j
  JOIN metrics_topic_quarter m
    ON (m.run_id, m.scope, m.topic_key, m.quarter, m.source, m.content_type,
        m.panel_version, m.panel_role)
     = (j.run_id, j.scope, j.topic_key, j.quarter, j.source, j.content_type,
        j.panel_version, j.panel_role)
 WHERE j.run_id = %(run_id)s AND j.scope = %(scope)s AND j.panel_version = %(panel_version)s
   AND j.source = %(source)s AND j.content_type = %(content_type)s AND j.panel_role = %(panel_role)s
"""


class NoCrosscheck(LookupError):
    """There is nothing to compare yet. Blocked rather than a failure, so in the CLI it is blocked(2)."""


@dataclass(frozen=True)
class Built:
    """One set of the three answers. Nothing is written, so this is the whole output."""

    run_id: int
    snapshot_id: int
    panel_version: int
    quarter: str
    quarters: tuple[str, ...]
    composition: tuple[SourceShare, ...]
    ratings: tuple[RatingRow, ...]
    ingredients: Ingredients
    documents: dict[str, int] = field(default_factory=dict)
    mention_documents: dict[str, int] = field(default_factory=dict)
    violations: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        """`ok` = the crosscheck table was computed. **A disagreement is not carried here.**

        That the sources disagree is the finding this command exists to give rather than a failure of the
        run, and that signal is already carried by the table's `reading` column. `partial` means only "do not
        trust this output" (the contract's §exit codes, the place #41 pinned in §Sensitivity and #6 in the
        cards).
        """
        return "ok" if not self.violations else "partial"

    @property
    def thin(self) -> int:
        return sum(1 for row in self.ratings if row.thin)

    @property
    def note(self) -> str:
        tail = f" partial:{len(self.violations)} violations" if self.violations else ""
        return (
            f"trend crosscheck run={self.run_id} snapshot={self.snapshot_id} "
            f"panel=v{self.panel_version} quarter={self.quarter} "
            f"topics={len(self.composition)} rated={len(self.ratings)} thin={self.thin} "
            f"ingredients={len(self.ingredients.rows)} formula={self.ingredients.formula_products}"
            f"{tail}"
        )


@dataclass(frozen=True)
class Outcome:
    """The same three columns as `quarter` · `judge` · `sensitivity` (note · status · violations). Only
    `lines` belongs to this command."""

    built: Built
    lines: tuple[str, ...] = ()

    @property
    def note(self) -> str:
        return self.built.note

    @property
    def status(self) -> str:
        return self.built.status

    @property
    def violations(self) -> tuple[str, ...]:
        return self.built.violations


def _table(schema: str, table: str) -> pgsql.Composed | pgsql.Identifier:
    """Production uses the trend_radar schema and the tests use a single schema of their own
    (tests/conftest.py)."""
    if not schema:
        return pgsql.Identifier(table)
    return pgsql.SQL("{}.{}").format(pgsql.Identifier(schema), pgsql.Identifier(table))


def sun_params() -> dict[str, Any]:
    """The arguments of the suncare population predicate. The holdout (#51) has to stand on this predicate too
    for the difference between the two arms to be the sample's rather than the filter's -- written in two
    places, the population splits quietly the day only one changes."""
    return {"board": SUN_BOARD, "category": list(SUN_CATEGORY)}


def commerce_sql(schema: str, statement: str) -> pgsql.Composed:
    """Binds the four commerce sources to that schema. It is public so the holdout does not write the same
    predicate again."""
    return pgsql.SQL(statement).format(  # pyright: ignore[reportArgumentType]
        rank=_table(schema, "rank_snapshot"),
        review=_table(schema, "review"),
        topic=_table(schema, "review_topic"),
        product=_table(schema, "product"),
    )


@dataclass(frozen=True)
class Read:
    run_id: int
    snapshot_id: int
    panel_version: int
    quarter: str
    quarters: tuple[str, ...]
    topic_keys: tuple[str, ...]
    judged: dict[str, tuple[int | None, float | None, float | None, str]]
    rated: dict[tuple[str, str, str], list[tuple[str, float]]]
    formula: list[tuple[str, str]]
    run_on_lists: int
    mentions: dict[str, dict[str, int]]
    talk: dict[str, dict[str, int]]
    documents: dict[str, int]


def _quarter_of(quarters: list[str]) -> str:
    """확정된 마지막 분기. 마지막은 판정이 `미확정(진행 중)` 으로 두는 진행 중 분기라 과소 집계된다."""
    return quarters[-2] if len(quarters) > 1 else quarters[-1]


def _judged(rows: list[tuple], quarter: str) -> dict[str, tuple[int | None, float | None, float | None, str]]:
    mine = [row for row in rows if row[0] == quarter]
    place = crosscheck.ranks({str(row[1]): float(row[4] or 0) for row in mine})
    return {
        str(topic): (
            place.get(str(topic)),
            None if composition is None else round(100 * float(composition), 2),
            None if gap is None else float(gap),
            str(trend_type),
        )
        for _q, topic, trend_type, gap, composition in mine
    }


def _scan(
    conn: psycopg.Connection[Any], dictionary: Any, topic_keys: tuple[str, ...], sun_reviews: set[str]
) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, int]], int]:
    """One scan of the chunks produces (source -> topic -> documents) · (ingredient -> discourse documents) ·
    the size of the commerce population.

    The two answers share one scan not only for cost -- scanned apart they could see the index at two
    different moments, and then "there is ingredient discourse but not that topic" cannot be told apart as an
    index difference or a fact.

    Only the commerce population holds a set of document ids. The document count on the YouTube side is a
    value the SQL counts and need not be held here, and holding 280k of them as a Python set costs more than
    the answer itself.

    **It commits per page.** Because the topic matching has to run outside the transaction -- scanned in one
    stream that transaction stays open until the matching ends (11.3 seconds over the whole set), and
    `needs_runtime`'s `transaction_timeout` (60 seconds) is a cap on the **total lifetime** of a transaction
    and cuts it mid-way as the corpus grows.
    """
    mentions: dict[str, dict[str, set[str]]] = {source: {} for source in crosscheck.SOURCES}
    talk: dict[str, dict[str, set[str]]] = {
        key: {"youtube": set(), "youtube_sun": set(), "commerce": set()} for key in crosscheck.INGREDIENT_KEYS
    }
    axis = set(topic_keys)
    counted: set[str] = set()
    cursor = ""
    while True:
        with conn.cursor() as cur:
            cur.execute(CHUNKS, (cursor, CHUNK_PAGE))
            rows = cur.fetchall()
        conn.commit()
        for _chunk_id, source, doc_id, text in rows:
            # The commerce population is decided by the ranking -- reviews of a product outside the suncare
            # boards do not enter this table.
            if source == crosscheck.COMMERCE_REVIEW:
                if doc_id not in sun_reviews:
                    continue
                counted.add(doc_id)
            if source in mentions:
                for topic in topic_registry.match_topics(text, dictionary=dictionary):
                    if topic in axis:
                        mentions[source].setdefault(topic, set()).add(doc_id)
            commerce = source == crosscheck.COMMERCE_REVIEW
            sunny = any(word in text for word in crosscheck.SUN_WORDS)
            for key, terms in crosscheck.INGREDIENT_KEYS.items():
                if not crosscheck.mentions_term(text, (key, *terms)):
                    continue
                talk[key]["commerce" if commerce else "youtube"].add(doc_id)
                # 선크림 문맥은 **같은 청크 안**에서 본다. 문서 단위로 보면 자막 한 편이 통째로
                # 선크림 문맥이 되고, 그 수는 아무것도 가려내지 못한다 (계약 §성분).
                if sunny and not commerce:
                    talk[key]["youtube_sun"].add(doc_id)
        if len(rows) < CHUNK_PAGE:
            break
        cursor = rows[-1][0]
    return (
        {source: {topic: len(docs) for topic, docs in found.items()} for source, found in mentions.items()},
        {key: {where: len(docs) for where, docs in found.items()} for key, found in talk.items()},
        len(counted),
    )


def load(
    conn: psycopg.Connection[Any],
    *,
    scope: str = SCOPE,
    commerce_schema: str | None = None,
    snapshot_id: int | None = None,
    panel_version: int | None = None,
) -> Read:
    """It reads. The run is found the same one way as `quarter` · `judge` · `sensitivity`.

    `commerce_schema` is resolved at call time -- `None` is "the deployment default (`trend_radar`)" and `""`
    is "whatever search_path knows". Nailed into an argument default, the two callers would not stay apart
    (the same convention as `--url` being `runtime_url()` when it is `None`).
    """
    commerce_schema = COMMERCE_SCHEMA if commerce_schema is None else commerce_schema
    dictionary = topic_registry.use_active(conn)
    topic_keys = tuple(entry["topic"] for entry in dictionary.entries if entry["trend_use"])
    where = sun_params()
    with conn.cursor() as cur:
        version = panel_version if panel_version is not None else panel_seed.active_version(cur)
        snapshot = snapshot_id if snapshot_id is not None else active_snapshot(cur)
        if version is None:
            raise NoPopulation("no active panel roster; run `python -m db.seed --only panel` first")
        if snapshot is None:
            raise NoPopulation("no active corpus snapshot; run `python -m db.corpus load <dir>` first")
        cur.execute(FIND_RUN, (note_of(scope, snapshot, version),))
        found = cur.fetchone()
        if found is None:
            raise NoCrosscheck(
                f"no quarter run for {scope!r} on snapshot {snapshot}; run `cosmai trend quarter`"
            )
        run_id = int(found[0])
        cur.execute(
            CELLS,
            {
                "run_id": run_id,
                "scope": scope,
                "panel_version": version,
                "source": COMMENT,
                "content_type": CONTENT_TYPE,
                "panel_role": PANEL_ROLE,
            },
        )
        cells = cur.fetchall()
        if not cells:
            raise NoCrosscheck(f"run {run_id} has no topic_quarter_judgement row; run `cosmai trend judge`")
        cur.execute(commerce_sql(commerce_schema, SUN_PRODUCTS), where)
        ranked = int((cur.fetchone() or (0,))[0])
        if not ranked:
            raise NoCrosscheck(
                "no suncare product in rank_snapshot; run `cosmai collect commerce` -- the ranking is "
                "what decides the commerce population (contracts/interfaces.md §Crosscheck)"
            )
        cur.execute(commerce_sql(commerce_schema, SUN_REVIEWS), where)
        sun_reviews = {corpus.review_doc_id(source, key) for source, key in cur.fetchall()}
        cur.execute(commerce_sql(commerce_schema, RATED), where)
        rated_rows = cur.fetchall()
        cur.execute(commerce_sql(commerce_schema, FORMULA), ())
        formula_rows = cur.fetchall()
        cur.execute(CHUNK_DOCS)
        documents = {str(source): int(count) for source, count in cur.fetchall()}
    conn.commit()
    if not documents:
        raise NoCrosscheck("needs.retrieval_chunk is empty; run `cosmai retrieval chunk`")

    quarters = sorted({str(row[0]) for row in cells})
    quarter = _quarter_of(quarters)
    mentions, talk, commerce_documents = _scan(conn, dictionary, topic_keys, sun_reviews)
    # The commerce document count is the size of **the population the ranking decided**, not the whole index.
    # Carrying the whole index would point the denominator of the share and the document count at different
    # populations.
    documents[crosscheck.COMMERCE_REVIEW] = commerce_documents
    names = [
        (f"{source}:{product}", name)
        for source, product, ingredients in formula_rows
        for name in crosscheck.parse_ingredients(ingredients)
    ]
    return Read(
        run_id=run_id,
        snapshot_id=snapshot,
        panel_version=version,
        quarter=quarter,
        quarters=tuple(quarters),
        topic_keys=topic_keys,
        judged=_judged(cells, quarter),
        rated=_latest(rated_rows),
        formula=names,
        run_on_lists=sum(1 for _product, name in names if crosscheck.run_on(name)),
        mentions=mentions,
        talk=talk,
        documents=documents,
    )


def _latest(rows: list[tuple]) -> dict[tuple[str, str, str], list[tuple[str, float]]]:
    """One row per (product, option) at the newest point in time only. Count them all and the product count is
    inflated by the number of points (the contract's §Rating)."""
    newest: dict[tuple[str, str, str, str], tuple] = {}
    for source, product, group, name, share, captured_at in rows:
        key = (str(source), str(product), str(group), str(name))
        current = newest.get(key)
        if current is None or captured_at > current[5]:
            newest[key] = (source, product, group, name, share, captured_at)
    made: dict[tuple[str, str, str], list[tuple[str, float]]] = {}
    for source, product, group, name, share, _at in newest.values():
        made.setdefault((str(source), str(product), str(group)), []).append((str(name), float(share)))
    return made


def build(
    conn: psycopg.Connection[Any],
    *,
    scope: str = SCOPE,
    commerce_schema: str | None = None,
    snapshot_id: int | None = None,
    panel_version: int | None = None,
) -> Built:
    read = load(
        conn,
        scope=scope,
        commerce_schema=commerce_schema,
        snapshot_id=snapshot_id,
        panel_version=panel_version,
    )
    audits = crosscheck.audit(read.formula)
    rows = tuple(
        crosscheck.IngredientRow(
            ingredient=key,
            talk_youtube=read.talk[key]["youtube"],
            talk_youtube_sun=read.talk[key]["youtube_sun"],
            talk_commerce=read.talk[key]["commerce"],
            reading=crosscheck.ingredient_reading(
                IngredientRow(
                    key,
                    read.talk[key]["youtube"],
                    read.talk[key]["youtube_sun"],
                    read.talk[key]["commerce"],
                )
            ),
        )
        for key in crosscheck.INGREDIENT_KEYS
    )
    ingredients = Ingredients(
        rows=rows,
        audits=audits,
        formula_products=len({product for product, _name in read.formula}),
        run_on_lists=read.run_on_lists,
        names=len({name for _product, name in read.formula}),
    )
    # Every violation line says the same thing: **do not trust this output.** A disagreement is not one of
    # them.
    violations = [
        f"key_mismatch {audit.key} caught {', '.join(audit.denied)} -- "
        f"{crosscheck.denial_reason(audit.key, audit.denied[0])}"
        for audit in ingredients.suspects
    ]
    violations += [
        f"group_map_drift {group} -> {topic} is not on the active topic axis"
        for group, topic in crosscheck.GROUP_MAP.items()
        if topic not in read.topic_keys
    ]
    return Built(
        run_id=read.run_id,
        snapshot_id=read.snapshot_id,
        panel_version=read.panel_version,
        quarter=read.quarter,
        quarters=read.quarters,
        composition=crosscheck.composition(read.mentions, read.topic_keys),
        ratings=crosscheck.ratings(read.rated, read.judged),
        ingredients=ingredients,
        documents=read.documents,
        mention_documents={
            source: sum(found.get(topic, 0) for topic in read.topic_keys)
            for source, found in read.mentions.items()
        },
        violations=tuple(violations),
    )


HEAD = ("댓글", "자막", "제목", "리뷰")
# There is an ingredient list whose single name is 384 characters (written with spaces and no commas,
# measured 2026-08-27). An audit line carrying it as it is cannot be read, so it is cut for display only --
# the counted value is the original. The width is counted in **screen cells** rather than characters: a
# Hangul character is two cells, so cutting by `len()` turns 34 characters into 60 screen cells and only that
# line runs outside the table.
NAME_WIDTH = 34


def _pad(text: str, width: int, *, right: bool = False) -> str:
    """Hangul takes two cells in a terminal. Python's field padding does not know that, so it is counted
    here."""
    space = " " * max(0, width - _width(text))
    return (space + text) if right else (text + space)


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def _short(name: str) -> str:
    if _width(name) <= NAME_WIDTH:
        return name
    kept: list[str] = []
    room = NAME_WIDTH - 1
    for char in name:
        room -= _width(char)
        if room < 0:
            break
        kept.append(char)
    return "".join(kept) + "…"


def render(built: Built) -> list[str]:
    """The answer a person reads. It emits the same sentences as the summaries of the three ydc scripts."""
    lines = [
        "구성  같은 사전·같은 단위(문서)·소스마다 자기 분모. 합산하지 않는다 · "
        + " · ".join(
            f"{name} {built.documents.get(source, 0):,}문서"
            for name, source in zip(HEAD, crosscheck.SOURCES, strict=True)
        ),
        "  " + _pad("", 18) + "".join(_pad(name, 10, right=True) for name in HEAD),
    ]
    for row in built.composition:
        cells = "".join(f"{row.shares[source]:>9.2f}%" for source in crosscheck.SOURCES)
        lines.append(f"  {_pad(row.topic_key, 18)}{cells}   {row.reading}")
    lines.append(
        f"평가  커머스 플랫폼 속성 평가 {len(built.ratings)}주제 "
        f"(제품 {crosscheck.MIN_PRODUCTS}개 미만이라 해석을 쓰지 않는 주제 {built.thin}) · "
        f"판정은 {built.quarter}"
    )
    for row in built.ratings:
        gap = "-" if row.youtube_gap_pp is None else f"{row.youtube_gap_pp:+.2f}"
        lines.append(
            f"  {_pad(row.topic_key, 18)}{row.products_rated:>4}제품 긍정 {row.positive_rate_mean:>5.1f}% "
            f"(중앙 {row.positive_rate_median:>5.1f}%) · 댓글 {row.youtube_rank_comment}위 "
            f"gap {gap}%p {row.youtube_trend_type} · {'|'.join(row.commerce_groups)}  {row.reading}"
        )
    lines.append(
        f"성분  담론 셋. 처방 축은 잠겨 있다(FORMULA_HOLD) · "
        f"성분표 {built.ingredients.formula_products}제품 · 고유 성분명 {built.ingredients.names:,} · "
        f"공백 나열 {built.ingredients.run_on_lists}"
    )
    for row in built.ingredients.rows:
        lines.append(
            f"  {_pad(row.ingredient, 18)}유튜브 {row.talk_youtube:>6,} "
            f"(선크림 문맥 {row.talk_youtube_sun:>5,} · {row.sun_share:>5.1f}%) · "
            f"리뷰 {row.talk_commerce:>5,}  {row.reading}"
        )
    lines.append("감사  키가 실제로 무엇을 잡는가 -- 수치만 봐서는 못 잡는다")
    for audit in built.ingredients.audits:
        names = " · ".join(f"{_short(name)}({count})" for name, count in audit.names) or "-"
        lines.append(f"  {_pad(audit.key, 18)}{audit.rows:>5}행 {audit.products:>4}제품  {names}")
    return lines


def run(
    conn: psycopg.Connection[Any],
    *,
    scope: str = SCOPE,
    commerce_schema: str | None = None,
    snapshot_id: int | None = None,
    panel_version: int | None = None,
) -> Outcome:
    built = build(
        conn,
        scope=scope,
        commerce_schema=commerce_schema,
        snapshot_id=snapshot_id,
        panel_version=panel_version,
    )
    return Outcome(built=built, lines=tuple(render(built)))


__all__ = [
    "COMMERCE_SCHEMA",
    "SUN_BOARD",
    "SUN_CATEGORY",
    "SUN_JOIN",
    "SUN_PRODUCTS",
    "Built",
    "NoCrosscheck",
    "NoPopulation",
    "Outcome",
    "Read",
    "build",
    "commerce_sql",
    "load",
    "render",
    "run",
    "sun_params",
]
