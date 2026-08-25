"""`analyze polarity` 한 단계 — 추출과 판정을 한 번에 돌려 need_mention·wish_mention 을 채운다 (T14).

진입점은 run(conn, ...) 하나다: cosmai/cli.py 의 stage 배선은 #5 가 세 유닛을 한 곳에서 묶는다.

needs_runtime 의 시간 제한(statement_timeout 30s · transaction_timeout 60s ·
idle_in_transaction 15s, db/bootstrap.sql)에 맞춰 읽기는 키셋 페이징으로, 쓰기는 배치 커밋으로
쪼갠다 — analysis/linker/pipeline.py 가 같은 제약을 같은 모양으로 푼다.
자기 버전 계열(rule-v*)의 행만 지우고 갱신한다: 시드(slice-*)는 삭제도 갱신도 되지 않는다
(삭제는 NEED_DELETE 의 LIKE 필터가, 삽입은 extractor_version 을 품은 005 의 자연키가 막는다).
같은 계열 안에서 두 극성 구현이 공존하는 자리는 scope 로 갈린다 — 소유 표(ownership.py)가 배정한
lexicon_category 는 그 주인만 쓰고 지운다.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import astuple, dataclass
from datetime import date
from typing import Any, LiteralString

import psycopg
from psycopg import sql as pgsql

from analysis.extractor import RuleExtractor
from analysis.lexicon import load_aspects, load_lexicon
from analysis.polarity import GENERIC_RULESET, SUNCARE_RULESET, RulePolarity, ruleset_for
from analysis.polarity.ownership import OWNERS, foreign_scopes
from analysis.types import (
    AspectLexicon,
    Candidate,
    Lexicon,
    NeedMentionRow,
    Polarity,
    PolarityRequest,
    PolarityResult,
    TextUnit,
    WishMentionRow,
)
from analysis.units import CategoryMap, comment_unit, load_category_map, month_of, review_unit

COMMERCE_SCHEMA = "trend_radar"
YOUTUBE_SCHEMA = "tubedepth"
BATCH = 2000  # #2 와 같은 값: 한 트랜잭션이 60s 안에 끝나는 크기
FIRST = ""  # 키셋 페이징의 첫 키 (원천 키는 전부 text 다)
FIVE = 5.0

REVIEW_COLUMNS = ("source", "product_key", "review_key", "rating", "body", "written_at", "captured_at")
REVIEW_KEY = ("source", "review_key")  # trend_radar.review 의 PK
REVIEW_KEY_AT = (0, 2)  # REVIEW_COLUMNS 안에서의 자리
COMMENT_COLUMNS = ("video_id", "comment_id", "text", "like_count", "published_at", "first_seen_at")
COMMENT_KEY = ("video_id", "comment_id")  # tubedepth.comments 의 PK
COMMENT_KEY_AT = (0, 1)

RUN_START: LiteralString = "INSERT INTO analysis_run (versions, note) VALUES (%s::jsonb, %s) RETURNING run_id"
RUN_END: LiteralString = (
    "UPDATE analysis_run SET finished_at = now(), status = 'ok', note = %s WHERE run_id = %s"
)
RUN_NOTE: LiteralString = "UPDATE analysis_run SET note = %s WHERE run_id = %s"
# `replace_stale` 의 DELETE 커밋과 그 달의 마지막 flush 사이가 그 달이 부분만 남아 있는 창이다. 실행이
# 그 안에서 죽으면 원천은 그대로여도 그 달의 need_mention 은 반쪽이고, 주인 있는 scope 는 규칙이
# 배제하므로(#31) 사람이 다시 돌릴 때까지 아무도 메우지 않는다. 창 안에 있다는 사실을 DB 가 말한다.
MARKER = "rewriting="
NEED_DELETE: LiteralString = """
DELETE FROM need_mention WHERE src = %s AND month = %s AND extractor_version LIKE 'rule-v%%'
AND NOT (extractor_version = %s AND polarity_version = %s)
AND (lexicon_category IS NULL OR lexicon_category <> ALL(%s::text[]))
"""
# 마지막 줄이 남의 scope(ownership.py)를 삭제 밖에 둔다 — 빈 배열이면 <> ALL 이 전부 참이라 옛 동작이다.
# --scope 실행이 다시 쓰는 것은 그 lexicon_category 뿐이다 — 삭제를 같이 좁히지 않으면 다시 쓰지 않을
# 행까지 지운다. 규칙으로 돌 때만 무해했다: polarity_version 이 바뀌는 순간 그 달 전체가 stale 이 된다.
NEED_DELETE_SCOPED: LiteralString = NEED_DELETE + "AND lexicon_category = %s\n"
WISH_DELETE: LiteralString = """
DELETE FROM wish_mention WHERE src = %s AND month = %s AND extractor_version LIKE 'rule-v%%'
AND extractor_version <> %s
"""
# 005 로 extractor_version 이 자연키에 들어간 뒤로 시드 행(slice-*)은 이 INSERT 와 애초에 충돌하지
# 않는다 — 충돌하는 행은 반드시 이 실행과 같은 버전이므로 DO UPDATE 에 버전 필터가 필요 없다.
NEED_UPSERT: LiteralString = """
INSERT INTO need_mention
  (src, site, ref, product_ref, source_product_key, category, lexicon_category, need_key, aspect_scope,
   polarity, strength, rating, observed_at, observed_at_resolution, month, sentence, kind, marker,
   polarity_reason, extractor_version, polarity_version)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (src, ref, need_key, extractor_version, md5(sentence)) DO UPDATE
SET site = EXCLUDED.site, product_ref = EXCLUDED.product_ref,
    source_product_key = EXCLUDED.source_product_key, category = EXCLUDED.category,
    lexicon_category = EXCLUDED.lexicon_category, aspect_scope = EXCLUDED.aspect_scope,
    polarity = EXCLUDED.polarity, strength = EXCLUDED.strength, rating = EXCLUDED.rating,
    observed_at = EXCLUDED.observed_at, observed_at_resolution = EXCLUDED.observed_at_resolution,
    month = EXCLUDED.month, kind = EXCLUDED.kind, marker = EXCLUDED.marker,
    polarity_reason = EXCLUDED.polarity_reason, extractor_version = EXCLUDED.extractor_version,
    polarity_version = EXCLUDED.polarity_version
WHERE need_mention.lexicon_category IS NULL OR need_mention.lexicon_category <> ALL(%s::text[])
"""
# 마지막 줄이 NEED_DELETE 와 같은 술어다 — 저장된 lexicon_category 가 남의 scope 면 갱신도 하지 않는다.
# 저장된 scope 와 지금 매핑이 갈리면(rank_snapshot 최신 행·category_map 이 매일 다시 계산한다) 이 실행은
# 그 문장을 자기 것으로 보고 다시 뽑는다: 두 구현이 같은 need_key 를 고르면 자연키가 통째로 겹쳐, 삭제를
# 피한 주인의 행을 제자리 upsert 가 갈아 끼운다. WISH_UPSERT 가 쓰는 그 자리(DO UPDATE ... WHERE)다.
WISH_UPSERT: LiteralString = """
INSERT INTO wish_mention
  (src, ref, video_id, channel_id, channel_is_brand_owner, product_ref, observed_at,
   observed_at_resolution, month, wish_class, brand, format, attribute, marker, sentence, like_count,
   extractor_version)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (src, ref) DO UPDATE
SET video_id = EXCLUDED.video_id, channel_id = EXCLUDED.channel_id, product_ref = EXCLUDED.product_ref,
    observed_at = EXCLUDED.observed_at, observed_at_resolution = EXCLUDED.observed_at_resolution,
    month = EXCLUDED.month, wish_class = EXCLUDED.wish_class, brand = EXCLUDED.brand,
    format = EXCLUDED.format, attribute = EXCLUDED.attribute, marker = EXCLUDED.marker,
    sentence = EXCLUDED.sentence, like_count = EXCLUDED.like_count,
    extractor_version = EXCLUDED.extractor_version
WHERE wish_mention.extractor_version LIKE 'rule-v%%'
"""


@dataclass(frozen=True)
class StageResult:
    run_id: int
    months: int
    units: int
    need_rows: int
    wish_rows: int
    replaced: int
    captured_at_fallbacks: int  # formats.md: 0 이 아니게 되는 순간이 시간 규칙을 다시 볼 때다
    polarity_version: str = RulePolarity.version

    @property
    def note(self) -> str:
        return (
            f"analyze:polarity:{self.polarity_version} units={self.units} need={self.need_rows} "
            f"wish={self.wish_rows} replaced={self.replaced} "
            f"captured_at_fallback={self.captured_at_fallbacks}"
        )


@dataclass(frozen=True)
class _Pending:
    """한 페이지분의 후보 — 판정은 페이지가 다 모인 뒤에 사전별로 한 번씩 간다 (classify_many)."""

    unit: TextUnit
    lexicon_category: str | None
    candidate: Candidate


def _rewriting(base: str, src: str, month: str, scope: str | None) -> str:
    return f"{base} {MARKER}{src}/{month}" + (f"/{scope}" if scope else "")


def _note(conn: psycopg.Connection[Any], run_id: int, note: str) -> None:
    with conn.cursor() as cur:
        cur.execute(RUN_NOTE, (note, run_id))
    conn.commit()


def _table(schema: str, table: str) -> pgsql.Composed:
    return pgsql.SQL("{}.{}").format(pgsql.Identifier(schema), pgsql.Identifier(table))


def _month(observed: str, fallback: str) -> pgsql.Composed:
    return pgsql.SQL("to_char(coalesce({}, {}), 'YYYY-MM')").format(
        pgsql.Identifier(observed), pgsql.Identifier(fallback)
    )


def _exists(conn: psycopg.Connection[Any], schema: str, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"{schema}.{table}",))
        row = cur.fetchone()
    conn.rollback()
    return bool(row and row[0])


def _months(
    conn: psycopg.Connection[Any], table: pgsql.Composed, observed: str, fallback: str, since: date | None
) -> list[str]:
    month = _month(observed, fallback)
    where = (
        pgsql.SQL("WHERE coalesce({}, {})::date >= %s").format(
            pgsql.Identifier(observed), pgsql.Identifier(fallback)
        )
        if since
        else pgsql.SQL("")
    )
    query = pgsql.SQL("SELECT DISTINCT {m} FROM {t} {w} ORDER BY 1").format(m=month, t=table, w=where)
    with conn.cursor() as cur:
        cur.execute(query, (since,) if since else ())
        found = [r[0] for r in cur.fetchall() if r[0]]
    conn.rollback()
    return found


def _pages(
    conn: psycopg.Connection[Any],
    table: pgsql.Composed,
    columns: Sequence[str],
    key: Sequence[str],
    key_at: Sequence[int],
    observed: str,
    fallback: str,
    month: str,
    since: date | None,
    batch: int,
) -> Iterator[list[tuple[Any, ...]]]:
    """한 달치를 PK 키셋으로 잘라 읽는다 — 한 달이 통째로 30s 안에 들어온다는 보장이 없다."""
    selected = pgsql.SQL(", ").join(pgsql.Identifier(c) for c in columns)
    ordering = pgsql.SQL(", ").join(pgsql.Identifier(c) for c in key)
    where = pgsql.SQL("({}) > ({}) AND {} = %s").format(
        ordering, pgsql.SQL(", ").join(pgsql.SQL("%s") for _ in key), _month(observed, fallback)
    )
    if since:
        where = pgsql.SQL("{} AND coalesce({}, {})::date >= %s").format(
            where, pgsql.Identifier(observed), pgsql.Identifier(fallback)
        )
    query = pgsql.SQL("SELECT {c} FROM {t} WHERE {w} ORDER BY {o} LIMIT %s").format(
        c=selected, t=table, w=where, o=ordering
    )
    cursor: tuple[Any, ...] = (FIRST,) * len(key)
    while True:
        params = (*cursor, month, since, batch) if since else (*cursor, month, batch)
        with conn.cursor() as cur:
            cur.execute(query, params)
            page = cur.fetchall()
        # 읽자마자 닫는다: 판정하는 동안 열려 있으면 idle_in_transaction 15s 가 세션을 끊는다.
        conn.rollback()
        if not page:
            return
        yield page
        if len(page) < batch:
            return
        cursor = tuple(page[-1][i] for i in key_at)


def _product_facts(
    conn: psycopg.Connection[Any], schema: str
) -> tuple[dict[tuple[str, str], str], dict[tuple[str, str], str]]:
    """제품의 최신 카테고리와 이름 — 리뷰 행에는 둘 다 없다 (p1 extract_candidates 와 같은 재료)."""
    categories: dict[tuple[str, str], str] = {}
    names: dict[tuple[str, str], str] = {}
    if _exists(conn, schema, "rank_snapshot"):
        with conn.cursor() as cur:
            cur.execute(
                pgsql.SQL(
                    "SELECT DISTINCT ON (source, product_key) source, product_key, category_name "
                    "FROM {} WHERE category_name IS NOT NULL "
                    "ORDER BY source, product_key, captured_at DESC"
                ).format(_table(schema, "rank_snapshot"))
            )
            categories = {(r[0], r[1]): r[2] for r in cur.fetchall()}
        conn.rollback()
    if _exists(conn, schema, "product"):
        with conn.cursor() as cur:
            cur.execute(
                pgsql.SQL(
                    "SELECT DISTINCT ON (source, product_key) source, product_key, name FROM {} "
                    "ORDER BY source, product_key, captured_at DESC"
                ).format(_table(schema, "product"))
            )
            names = {(r[0], r[1]): r[2] for r in cur.fetchall()}
        conn.rollback()
    return categories, names


def _channels(conn: psycopg.Connection[Any], schema: str) -> dict[str, tuple[str | None, int | None]]:
    if not _exists(conn, schema, "video_snapshots"):
        return {}
    with conn.cursor() as cur:
        cur.execute(
            pgsql.SQL(
                "SELECT DISTINCT ON (video_id) video_id, channel_id, view_count FROM {} "
                "ORDER BY video_id, fetched_at DESC"
            ).format(_table(schema, "video_snapshots"))
        )
        found = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    conn.rollback()
    return found


class PolarityStage:
    """사전·규칙을 한 번만 만들고 src×월 배치를 돌린다."""

    def __init__(
        self,
        conn: psycopg.Connection[Any],
        batch: int = BATCH,
        polarity: Polarity | None = None,
        owners: Mapping[str, str] = OWNERS,
    ) -> None:
        self.conn = conn
        self.batch = batch
        self.extractor = RuleExtractor()
        # 규칙 인스턴스는 판정자가 바뀌어도 남는다: aspect_scope 는 사전이 말하는 사실이지 판정 결과가 아니다.
        self.rule = RulePolarity()
        self.polarity: Polarity = polarity or self.rule
        # 다른 구현이 주인인 scope — 이 실행은 그 자리를 쓰지도 지우지도 않는다 (ownership.py).
        self.foreign = foreign_scopes(owners, self.polarity.version)
        self.aspects: dict[str, AspectLexicon] = {
            name: load_aspects(conn, name) for name in (SUNCARE_RULESET, GENERIC_RULESET)
        }
        self.lexicon: Lexicon = load_lexicon(conn)
        self.categories: CategoryMap = load_category_map(conn)
        conn.rollback()

    def versions(self) -> dict[str, Any]:
        return {
            "extractor": RuleExtractor.version,
            "polarity": self.polarity.version,
            "lexicon": {"entity": self.lexicon.version, "aspect": self.aspects[GENERIC_RULESET].version},
        }

    def _scope_of(self, aspects: AspectLexicon, category: str | None, aspect: str) -> str:
        for pattern in self.rule.patterns_for(aspects, category):
            if pattern.aspect == aspect:
                return pattern.scope
        return "generic"

    def candidates(self, unit: TextUnit, lexicon_category: str | None) -> list[_Pending]:
        aspects = self.aspects[ruleset_for(lexicon_category)]
        return [
            _Pending(unit, lexicon_category, candidate)
            for candidate in self.extractor.candidates(unit, aspects, lexicon_category)
        ]

    def need_rows(self, pending: Sequence[_Pending]) -> list[NeedMentionRow]:
        """사전별로 묶어 classify_many 한 번씩 — 배치 API 를 가진 구현(#6)은 문장마다 왕복하면 정가다."""
        grouped: dict[str, list[int]] = {}
        for i, item in enumerate(pending):
            grouped.setdefault(ruleset_for(item.lexicon_category), []).append(i)
        rows: list[NeedMentionRow | None] = [None] * len(pending)
        for ruleset, indexes in grouped.items():
            aspects = self.aspects[ruleset]
            found = self.polarity.classify_many(
                [
                    PolarityRequest(
                        pending[i].candidate.sentence, pending[i].unit.rating, pending[i].lexicon_category
                    )
                    for i in indexes
                ],
                aspects,
            )
            for i, result in zip(indexes, found, strict=True):
                rows[i] = self._row(pending[i], result, aspects)
        return [row for row in rows if row is not None]

    def _row(self, item: _Pending, found: PolarityResult, aspects: AspectLexicon) -> NeedMentionRow:
        unit = item.unit
        strength = (
            round(1 - unit.rating / FIVE, 2)
            if unit.src == "review" and unit.rating is not None
            else unit.like_count
        )
        return NeedMentionRow(
            src=unit.src,
            site=unit.site,
            ref=unit.ref,
            product_ref=None,  # #2 의 linker 가 analyze link 에서 채운다
            source_product_key=unit.product_key,
            category=unit.category,
            lexicon_category=item.lexicon_category,
            need_key=found.aspect or "",  # B8: aspect 없음은 '' 센티널
            aspect_scope=self._scope_of(aspects, item.lexicon_category, found.aspect)
            if found.aspect
            else None,
            polarity=found.polarity,
            strength=strength,
            rating=unit.rating,
            observed_at=unit.observed_at,
            observed_at_resolution=unit.observed_at_resolution,
            month=month_of(unit.observed_at),
            sentence=item.candidate.sentence,
            kind=item.candidate.kind,
            marker=item.candidate.marker,
            polarity_reason=found.reason,
            extractor_version=RuleExtractor.version,
            polarity_version=self.polarity.version,
        )

    def wish_row(self, unit: TextUnit) -> WishMentionRow | None:
        found = self.extractor.wishes(unit, self.lexicon)
        if found is None:
            return None
        return WishMentionRow(
            src=unit.src,
            ref=unit.ref,
            video_id=unit.ref.split("/", 1)[0],
            channel_id=unit.channel_id,
            channel_is_brand_owner=None,  # 브랜드 채널 판정은 링커(#2)의 브랜드 사전이 필요하다
            product_ref=None,
            observed_at=unit.observed_at,
            observed_at_resolution=unit.observed_at_resolution,
            month=month_of(unit.observed_at),
            wish_class=found.wish_class,
            brand=found.brand,
            format=found.format,
            attribute=found.attribute,
            marker=found.marker,
            sentence=found.sentence,
            like_count=unit.like_count,
            extractor_version=RuleExtractor.version,
        )

    def replace_stale(self, src: str, month: str, scope: str | None = None) -> int:
        """자기 버전 계열의 옛 행 중 이 실행이 다시 쓸 것만 지운다 — 그 자체로 한 트랜잭션이다."""
        versions = (RuleExtractor.version, self.polarity.version, list(self.foreign))
        with self.conn.cursor() as cur:
            if scope is None:
                cur.execute(NEED_DELETE, (src, month, *versions))
            else:
                cur.execute(NEED_DELETE_SCOPED, (src, month, *versions, scope))
            replaced = cur.rowcount
            # wish_mention 에는 lexicon_category 가 없고 스코프 실행은 wish 행을 하나도 만들지 않는다.
            if scope is None:
                cur.execute(WISH_DELETE, (src, month, RuleExtractor.version))
                replaced += cur.rowcount
        self.conn.commit()
        return replaced

    def _write(self, statement: LiteralString, rows: Sequence[Any], extra: tuple[Any, ...] = ()) -> None:
        # INSERT 의 컬럼 순서 = 계약 dataclass 의 필드 순서 (interfaces.md) + DO UPDATE 의 술어 인자.
        for start in range(0, len(rows), self.batch):
            with self.conn.cursor() as cur:
                cur.executemany(statement, [astuple(r) + extra for r in rows[start : start + self.batch]])
            self.conn.commit()

    def flush(self, needs: Sequence[NeedMentionRow], wishes: Sequence[WishMentionRow]) -> None:
        self._write(NEED_UPSERT, needs, (list(self.foreign),))
        self._write(WISH_UPSERT, wishes)


def run(
    conn: psycopg.Connection[Any],
    *,
    since: date | None = None,
    scope: str | None = None,
    commerce_schema: str = COMMERCE_SCHEMA,
    youtube_schema: str = YOUTUBE_SCHEMA,
    batch: int = BATCH,
    polarity: Polarity | None = None,
    owners: Mapping[str, str] = OWNERS,
    on_run_open: Callable[[int], None] | None = None,
) -> StageResult:
    """`on_run_open` 은 run 행이 열린 그 순간 호출자에게 run_id 를 넘긴다 — 이 단계 안에서 죽어도
    호출자가 *자기* run 을 안다. 그것을 모르면 닫을 행을 표에서 되찾아야 하고, 동시에 도는 남의 run 과
    구별할 단서가 표에 없다 (analysis/pipeline.py)."""
    stage = PolarityStage(conn, batch, polarity, owners)
    version = stage.polarity.version
    if scope is not None and scope in stage.foreign:
        # 조용한 무동작이 아니라 거절이다 — `--impl` 을 빠뜨린 손실행이 여기서 멈춰야 표를 본다.
        raise ValueError(
            f"{scope} is owned by {owners[scope]}, not {version} (analysis/polarity/ownership.py)"
        )
    with conn.cursor() as cur:
        cur.execute(
            RUN_START,
            (json.dumps(stage.versions(), ensure_ascii=False), f"analyze:polarity:{version}"),
        )
        row = cur.fetchone()
    run_id = int(row[0]) if row else 0
    conn.commit()
    if on_run_open is not None:
        on_run_open(run_id)
    base_note = f"analyze:polarity:{version}"

    months = units = need_rows = wish_rows = replaced = fallbacks = 0

    if _exists(conn, commerce_schema, "review"):
        categories, names = _product_facts(conn, commerce_schema)
        table = _table(commerce_schema, "review")
        for month in _months(conn, table, "written_at", "captured_at", since):
            months += 1
            _note(conn, run_id, _rewriting(base_note, "review", month, scope))
            replaced += stage.replace_stale("review", month, scope)
            for page in _pages(
                conn,
                table,
                REVIEW_COLUMNS,
                REVIEW_KEY,
                REVIEW_KEY_AT,
                "written_at",
                "captured_at",
                month,
                since,
                batch,
            ):
                pending: list[_Pending] = []
                for source, product_key, review_key, rating, body, written_at, captured_at in page:
                    fallbacks += written_at is None
                    unit = review_unit(
                        source=source,
                        product_key=product_key,
                        review_key=review_key,
                        body=body,
                        rating=rating,
                        written_at=written_at,
                        captured_at=captured_at,
                        category=categories.get((source, product_key)),
                    )
                    lexicon_category = stage.categories.lexicon_category(
                        source, unit.category, names.get((source, product_key))
                    )
                    # 주인이 따로 있는 scope 는 판정 자체를 하지 않는다: 자연키에 polarity_version 이
                    # 없어 여기서 한 줄만 흘러도 upsert 가 주인의 라벨을 제자리에서 덮는다.
                    if lexicon_category in stage.foreign or (scope and lexicon_category != scope):
                        continue
                    units += 1
                    pending.extend(stage.candidates(unit, lexicon_category))
                needs = stage.need_rows(pending)
                need_rows += len(needs)
                stage.flush(needs, ())
            _note(conn, run_id, base_note)

    # 댓글에는 제품 카테고리가 없어 스코프 실행은 여기서 한 행도 만들 수 없다 — 그런 실행이 이 가지에
    # 들어가면 지우기만 하고 나온다 (yt_comment 의 need 행과 wish 행이 그 달에서 사라진다).
    if scope is None and _exists(conn, youtube_schema, "comments"):
        videos = _channels(conn, youtube_schema)
        table = _table(youtube_schema, "comments")
        for month in _months(conn, table, "published_at", "first_seen_at", since):
            months += 1
            _note(conn, run_id, _rewriting(base_note, "yt_comment", month, None))
            replaced += stage.replace_stale("yt_comment", month)
            for page in _pages(
                conn,
                table,
                COMMENT_COLUMNS,
                COMMENT_KEY,
                COMMENT_KEY_AT,
                "published_at",
                "first_seen_at",
                month,
                since,
                batch,
            ):
                pending = []
                wishes: list[WishMentionRow] = []
                for video_id, comment_id, text, like_count, published_at, first_seen_at in page:
                    fallbacks += published_at is None
                    channel_id, view_count = videos.get(video_id, (None, None))
                    unit = comment_unit(
                        video_id=video_id,
                        comment_id=comment_id,
                        text=text,
                        like_count=like_count,
                        published_at=published_at,
                        first_seen_at=first_seen_at,
                        channel_id=channel_id,
                        view_count=view_count,
                    )
                    # 댓글에는 제품 카테고리가 없다 — 카테고리 사전 없이 generic 규칙만 돈다.
                    units += 1
                    pending.extend(stage.candidates(unit, None))
                    wish = stage.wish_row(unit)
                    if wish is not None:
                        wishes.append(wish)
                needs = stage.need_rows(pending)
                need_rows += len(needs)
                wish_rows += len(wishes)
                stage.flush(needs, wishes)
            _note(conn, run_id, base_note)

    result = StageResult(
        run_id=run_id,
        months=months,
        units=units,
        need_rows=need_rows,
        wish_rows=wish_rows,
        replaced=replaced,
        captured_at_fallbacks=fallbacks,
        polarity_version=version,
    )
    with conn.cursor() as cur:
        cur.execute(RUN_END, (result.note, run_id))
    conn.commit()
    return result
