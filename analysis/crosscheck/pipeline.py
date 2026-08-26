"""청크 색인 + 커머스 원천 + 그 run 의 판정 → 소스 대조 세 답 (포크 #7).

**이 파이프라인은 아무것도 쓰지 않는다.** 세 답의 행은 (주제) 또는 (성분) 하나가 키인데 022 의 분기
입자는 여덟 칸이 키이고, 커머스 쪽에는 그중 분기도 명부도 없다 (`contracts/interfaces.md` §대조). 그래서
산출은 표가 아니라 답이고, 읽기 전용이라 운영 DB 에 그대로 돌린다.

청크 색인을 한 번 훑는다. 한 흐름으로 훑으면 그 트랜잭션이 매칭이 끝날 때까지 열려 있는데 `needs_runtime`
의 `transaction_timeout`(60초)은 트랜잭션 **총 수명**의 상한이라 도중에 끊는다 -- 그래서 키셋으로 한
페이지씩 받고 페이지마다 커밋한다(`analysis/retrieval/eval.py` 의 `gold_from_chunks` 와 같은 방식, 같은
이유). 전량 381,950청크 48MB 11.3초로 실제로 재 봤다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, LiteralString

import psycopg
from psycopg import sql as pgsql

from analysis import crosscheck
from analysis.crosscheck import IngredientRow, Ingredients, RatingRow, SourceShare
from analysis.retrieval import corpus
from analysis.retrieval import topics as topic_registry
from analysis.trend.pipeline import COMMENT, SCOPE, NoPopulation, note_of
from db.corpus import active_snapshot
from db.seed import panel as panel_seed

COMMERCE_SCHEMA = "trend_radar"
# 커머스 쪽 선케어 모집단은 **랭킹이 정한다.** 이름 부분문자열로 고르는 것은 §성분 의 `시카` 사고와 같은
# 실수다 -- 짧은 별칭이 다른 것을 잡는다. 여기서는 플랫폼이 선케어 보드·카테고리에 올린 제품이 답이다.
SUN_BOARD = "suncare"
SUN_CATEGORY = ("%선케어%", "%선크림%", "%선블록%", "%선스틱%", "%선쿠션%")
CHUNK_PAGE = 20_000

_SUN = """
  JOIN (SELECT DISTINCT source, product_key FROM {rank}
         WHERE board = %(board)s OR category_name ILIKE ANY(%(category)s)) sun
    USING (source, product_key)
"""
SUN_REVIEWS = "SELECT r.source, r.review_key FROM {review} r" + _SUN
RATED = (
    "SELECT t.source, t.product_key, t.topic_group, t.topic_name, t.share_pct, t.captured_at "
    # `share_pct` 가 NULL 인 소스는 비중 대신 가중치(`score`)를 싣는다. 가중치와 백분율은 다른 단위라
    # 섞어 평균 내면 아무것도 보여 주지 않고 틀린다 (`review_topic.score` 의 DDL 주석).
    "FROM {topic} t" + _SUN + " WHERE t.share_pct IS NOT NULL"
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
# 판정과 지표를 한 번에. 순위·구성비는 판정 표에 없고 지표 표에 있다 (024 는 세는 칸을 들지 않는다).
CELLS: LiteralString = """
SELECT j.quarter, j.topic_key, j.trend_type, j.gap_pp, m.composition
  FROM topic_quarter_judgement j
  JOIN metrics_topic_quarter m
    ON (m.run_id, m.scope, m.topic_key, m.quarter, m.source, m.content_type,
        m.panel_version, m.panel_role)
     = (j.run_id, j.scope, j.topic_key, j.quarter, j.source, j.content_type,
        j.panel_version, j.panel_role)
 WHERE j.run_id = %(run_id)s AND j.scope = %(scope)s AND j.panel_version = %(panel_version)s
   AND j.source = %(source)s
"""


class NoCrosscheck(LookupError):
    """대조할 것이 아직 없다. 실패가 아니라 막힘이라 CLI 에서는 blocked(2) 다."""


@dataclass(frozen=True)
class Built:
    """세 답 한 벌. 아무것도 쓰지 않으므로 이것이 산출의 전부다."""

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
        """`ok` = 대조표가 계산됐다. **어긋남은 여기 실리지 않는다.**

        소스가 어긋난다는 것은 이 명령이 답하려고 존재하는 발견이지 실행의 실패가 아니고, 그 신호는 표의
        `reading` 열이 이미 싣는다. `partial` 은 "이 산출을 믿지 마라" 하나만 뜻한다 (계약 §종료 코드,
        #41 이 §민감도 에서, #6 이 카드에서 못 박은 그 자리).
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
    """`quarter`·`judge`·`sensitivity` 와 같은 세 칸(note·status·violations). `lines` 만 이 명령의 것이다."""

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
    """운영은 trend_radar 스키마를, 테스트는 검사용 스키마 하나를 쓴다 (tests/conftest.py)."""
    if not schema:
        return pgsql.Identifier(table)
    return pgsql.SQL("{}.{}").format(pgsql.Identifier(schema), pgsql.Identifier(table))


def _commerce(schema: str, statement: str) -> pgsql.Composed:
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
    """청크를 한 번 훑어 (소스 -> 주제 -> 문서 수)·(성분 -> 담론 문서 수)·커머스 모집단 크기를 낸다.

    두 답이 같은 훑기를 나눠 쓰는 것은 비용 때문만이 아니다 -- 따로 훑으면 두 답이 다른 시점의 색인을
    볼 수 있고, 그때 "성분 담론은 있는데 그 주제는 없다" 가 색인 차이인지 사실인지 갈리지 않는다.

    커머스 모집단만 문서 id 집합을 든다. 유튜브 쪽 문서 수는 SQL 이 세는 값이라 여기서 들 필요가 없고,
    28만 개를 파이썬 집합으로 들면 그 자체가 답보다 큰 비용이다.
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
            # 커머스 쪽 모집단은 랭킹이 정한다 -- 선케어 보드 밖 제품의 리뷰는 이 표에 들지 않는다.
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
                if not crosscheck.matches(text, (key, *terms)):
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
    commerce_schema: str = COMMERCE_SCHEMA,
    snapshot_id: int | None = None,
    panel_version: int | None = None,
) -> Read:
    """읽는다. run 을 찾는 길은 `quarter`·`judge`·`sensitivity` 와 같은 하나다."""
    dictionary = topic_registry.use_active(conn)
    topic_keys = tuple(entry["topic"] for entry in dictionary.entries if entry["trend_use"])
    where = {"board": SUN_BOARD, "category": list(SUN_CATEGORY)}
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
        cur.execute(CELLS, {"run_id": run_id, "scope": scope, "panel_version": version, "source": COMMENT})
        cells = cur.fetchall()
        if not cells:
            raise NoCrosscheck(f"run {run_id} has no topic_quarter_judgement row; run `cosmai trend judge`")
        cur.execute(_commerce(commerce_schema, SUN_PRODUCTS), where)
        ranked = int((cur.fetchone() or (0,))[0])
        if not ranked:
            raise NoCrosscheck(
                "no suncare product in rank_snapshot; run `cosmai collect commerce` -- the ranking is "
                "what decides the commerce population (contracts/interfaces.md §대조)"
            )
        cur.execute(_commerce(commerce_schema, SUN_REVIEWS), where)
        sun_reviews = {corpus.review_doc_id(source, key) for source, key in cur.fetchall()}
        cur.execute(_commerce(commerce_schema, RATED), where)
        rated_rows = cur.fetchall()
        cur.execute(_commerce(commerce_schema, FORMULA), ())
        formula_rows = cur.fetchall()
        cur.execute(CHUNK_DOCS)
        documents = {str(source): int(count) for source, count in cur.fetchall()}
    conn.commit()
    if not documents:
        raise NoCrosscheck("needs.retrieval_chunk is empty; run `cosmai retrieval chunk`")

    quarters = sorted({str(row[0]) for row in cells})
    quarter = _quarter_of(quarters)
    mentions, talk, commerce_documents = _scan(conn, dictionary, topic_keys, sun_reviews)
    # 커머스 쪽 문서 수는 색인 전체가 아니라 **랭킹이 정한 모집단**의 크기다. 색인 전체를 실으면
    # 구성비의 분모와 문서 수가 다른 모집단을 가리키게 된다.
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
    """(제품, 선택지)별 최신 시점 한 행만. 전부 세면 제품 수가 시점 수만큼 부풀려진다 (계약 §평가)."""
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
    commerce_schema: str = COMMERCE_SCHEMA,
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
    # 위반 줄은 전부 같은 말을 한다: **이 산출을 믿지 마라.** 어긋남은 여기 들지 않는다.
    violations = [
        f"key_mismatch {audit.key} caught {', '.join(audit.denied)} -- "
        f"{crosscheck.DENIED_NAMES[audit.denied[0]]}"
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


def render(built: Built) -> list[str]:
    """사람이 읽는 답. ydc 세 스크립트의 요약과 같은 문장을 낸다."""
    lines = [
        "구성  같은 사전·같은 단위(문서)·소스마다 자기 분모. 합산하지 않는다",
        "      "
        + "".join(f"{name:>10}" for name in HEAD)
        + "   "
        + " · ".join(
            f"{name} {built.documents.get(source, 0):,}문서"
            for name, source in zip(HEAD, crosscheck.SOURCES, strict=True)
        ),
    ]
    for row in built.composition:
        cells = "".join(f"{row.shares[source]:>9.2f}%" for source in crosscheck.SOURCES)
        lines.append(f"  {row.topic_key:<16}{cells}   {row.reading}")
    lines.append(
        f"평가  커머스 플랫폼 속성 평가 {len(built.ratings)}주제 "
        f"(제품 {crosscheck.MIN_PRODUCTS}개 미만이라 해석을 쓰지 않는 주제 {built.thin}) · "
        f"판정은 {built.quarter}"
    )
    for row in built.ratings:
        gap = "-" if row.youtube_gap_pp is None else f"{row.youtube_gap_pp:+.2f}"
        lines.append(
            f"  {row.topic_key:<16}{row.products_rated:>4}제품 긍정 {row.positive_rate_mean:>5.1f}% "
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
            f"  {row.ingredient:<16}유튜브 {row.talk_youtube:>6,} (선크림 문맥 {row.talk_youtube_sun:>5,} · "
            f"{row.sun_share:>5.1f}%) · 리뷰 {row.talk_commerce:>5,}  {row.reading}"
        )
    lines.append("감사  키가 실제로 무엇을 잡는가 -- 수치만 봐서는 못 잡는다")
    for audit in built.ingredients.audits:
        names = " · ".join(f"{name}({count})" for name, count in audit.names) or "-"
        lines.append(f"  {audit.key:<16}{audit.rows:>5}행 {audit.products:>4}제품  {names}")
    return lines


def run(
    conn: psycopg.Connection[Any],
    *,
    scope: str = SCOPE,
    commerce_schema: str = COMMERCE_SCHEMA,
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
    "Built",
    "NoCrosscheck",
    "NoPopulation",
    "Outcome",
    "Read",
    "build",
    "load",
    "render",
    "run",
]
