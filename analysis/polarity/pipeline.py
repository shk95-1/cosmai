"""`analyze polarity` 한 단계 — 추출과 판정을 한 번에 돌려 need_mention·wish_mention 을 채운다 (T14).

진입점은 run(conn, ...) 하나다: cosmai/cli.py 의 stage 배선은 #5 가 세 유닛을 한 곳에서 묶는다.

트랜잭션은 src×월로 쪼갠다: needs_runtime 은 statement_timeout 30s · transaction_timeout 60s 다
(db/bootstrap.sql). 자기 버전 계열(rule-v*)의 옛 행만 지운다 — 시드(slice-*)는 남는다.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import astuple, dataclass
from datetime import date
from typing import Any, LiteralString

import psycopg
from psycopg import sql as pgsql

from analysis.extractor import RuleExtractor
from analysis.lexicon import load_aspects, load_lexicon
from analysis.polarity import GENERIC_RULESET, SUNCARE_RULESET, RulePolarity, ruleset_for
from analysis.types import AspectLexicon, Lexicon, NeedMentionRow, TextUnit, WishMentionRow
from analysis.units import CategoryMap, comment_unit, load_category_map, month_of, review_unit

COMMERCE_SCHEMA = "trend_radar"
YOUTUBE_SCHEMA = "tubedepth"
OWN_VERSIONS = "rule-v%"  # 이 유닛이 만든 행만 재생성 대상이다
FIVE = 5.0


RUN_START: LiteralString = "INSERT INTO analysis_run (versions, note) VALUES (%s::jsonb, %s) RETURNING run_id"
RUN_END: LiteralString = (
    "UPDATE analysis_run SET finished_at = now(), status = 'ok', note = %s WHERE run_id = %s"
)
NEED_DELETE: LiteralString = """
DELETE FROM need_mention WHERE src = %s AND month = %s AND extractor_version LIKE 'rule-v%%'
AND NOT (extractor_version = %s AND polarity_version = %s)
"""
WISH_DELETE: LiteralString = """
DELETE FROM wish_mention WHERE src = %s AND month = %s AND extractor_version LIKE 'rule-v%%'
AND extractor_version <> %s
"""
NEED_UPSERT: LiteralString = """
INSERT INTO need_mention
  (src, site, ref, product_ref, source_product_key, category, lexicon_category, need_key, aspect_scope,
   polarity, strength, rating, observed_at, observed_at_resolution, month, sentence, kind, marker,
   polarity_reason, extractor_version, polarity_version)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (src, ref, need_key, sentence) DO UPDATE
SET site = EXCLUDED.site, product_ref = EXCLUDED.product_ref,
    source_product_key = EXCLUDED.source_product_key, category = EXCLUDED.category,
    lexicon_category = EXCLUDED.lexicon_category, aspect_scope = EXCLUDED.aspect_scope,
    polarity = EXCLUDED.polarity, strength = EXCLUDED.strength, rating = EXCLUDED.rating,
    observed_at = EXCLUDED.observed_at, observed_at_resolution = EXCLUDED.observed_at_resolution,
    month = EXCLUDED.month, kind = EXCLUDED.kind, marker = EXCLUDED.marker,
    polarity_reason = EXCLUDED.polarity_reason, extractor_version = EXCLUDED.extractor_version,
    polarity_version = EXCLUDED.polarity_version
"""
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

    @property
    def note(self) -> str:
        return (
            f"analyze:polarity:{RulePolarity.version} units={self.units} need={self.need_rows} "
            f"wish={self.wish_rows} replaced={self.replaced} "
            f"captured_at_fallback={self.captured_at_fallbacks}"
        )


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
        return [r[0] for r in cur.fetchall() if r[0]]


def _rows(
    conn: psycopg.Connection[Any],
    table: pgsql.Composed,
    columns: Sequence[str],
    observed: str,
    fallback: str,
    month: str,
    since: date | None,
) -> list[tuple[Any, ...]]:
    selected = pgsql.SQL(", ").join(pgsql.Identifier(c) for c in columns)
    where = pgsql.SQL("{} = %s").format(_month(observed, fallback))
    if since:
        where = pgsql.SQL("{} AND coalesce({}, {})::date >= %s").format(
            where, pgsql.Identifier(observed), pgsql.Identifier(fallback)
        )
    query = pgsql.SQL("SELECT {c} FROM {t} WHERE {w}").format(c=selected, t=table, w=where)
    with conn.cursor() as cur:
        cur.execute(query, (month, since) if since else (month,))
        return cur.fetchall()


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
    if _exists(conn, schema, "product"):
        with conn.cursor() as cur:
            cur.execute(
                pgsql.SQL(
                    "SELECT DISTINCT ON (source, product_key) source, product_key, name FROM {} "
                    "ORDER BY source, product_key, captured_at DESC"
                ).format(_table(schema, "product"))
            )
            names = {(r[0], r[1]): r[2] for r in cur.fetchall()}
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
        return {r[0]: (r[1], r[2]) for r in cur.fetchall()}


class PolarityStage:
    """사전·규칙을 한 번만 만들고 src×월 배치를 돌린다."""

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self.conn = conn
        self.extractor = RuleExtractor()
        self.rule = RulePolarity()
        self.aspects: dict[str, AspectLexicon] = {
            name: load_aspects(conn, name) for name in (SUNCARE_RULESET, GENERIC_RULESET)
        }
        self.lexicon: Lexicon = load_lexicon(conn)
        self.categories: CategoryMap = load_category_map(conn)

    def versions(self) -> dict[str, Any]:
        return {
            "extractor": RuleExtractor.version,
            "polarity": RulePolarity.version,
            "lexicon": {"entity": self.lexicon.version, "aspect": self.aspects[GENERIC_RULESET].version},
        }

    def _scope_of(self, aspects: AspectLexicon, category: str | None, aspect: str) -> str:
        for pattern in self.rule.patterns_for(aspects, category):
            if pattern.aspect == aspect:
                return pattern.scope
        return "generic"

    def need_rows(self, unit: TextUnit, lexicon_category: str | None) -> Iterator[NeedMentionRow]:
        aspects = self.aspects[ruleset_for(lexicon_category)]
        for candidate in self.extractor.candidates(unit, aspects, lexicon_category):
            found = self.rule.classify(candidate.sentence, unit.rating, lexicon_category, aspects)
            strength = (
                round(1 - unit.rating / FIVE, 2)
                if unit.src == "review" and unit.rating is not None
                else unit.like_count
            )
            yield NeedMentionRow(
                src=unit.src,
                site=unit.site,
                ref=unit.ref,
                product_ref=None,  # #2 의 linker 가 analyze link 에서 채운다
                source_product_key=unit.product_key,
                category=unit.category,
                lexicon_category=lexicon_category,
                need_key=found.aspect or "",  # B8: aspect 없음은 '' 센티널
                aspect_scope=self._scope_of(aspects, lexicon_category, found.aspect)
                if found.aspect
                else None,
                polarity=found.polarity,
                strength=strength,
                rating=unit.rating,
                observed_at=unit.observed_at,
                observed_at_resolution=unit.observed_at_resolution,
                month=month_of(unit.observed_at),
                sentence=candidate.sentence,
                kind=candidate.kind,
                marker=candidate.marker,
                polarity_reason=found.reason,
                extractor_version=RuleExtractor.version,
                polarity_version=RulePolarity.version,
            )

    def wish_row(self, unit: TextUnit) -> WishMentionRow | None:
        found = self.extractor.wishes(unit, self.lexicon)
        if found is None:
            return None
        video_id = unit.ref.split("/", 1)[0]
        return WishMentionRow(
            src=unit.src,
            ref=unit.ref,
            video_id=video_id,
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

    def write(
        self, src: str, month: str, needs: Sequence[NeedMentionRow], wishes: Sequence[WishMentionRow]
    ) -> int:
        with self.conn.cursor() as cur:
            cur.execute(NEED_DELETE, (src, month, RuleExtractor.version, RulePolarity.version))
            replaced = cur.rowcount
            cur.execute(WISH_DELETE, (src, month, RuleExtractor.version))
            replaced += cur.rowcount
            if needs:
                # INSERT 의 컬럼 순서 = 계약 dataclass 의 필드 순서 (interfaces.md).
                cur.executemany(NEED_UPSERT, [astuple(r) for r in needs])
            if wishes:
                cur.executemany(WISH_UPSERT, [astuple(r) for r in wishes])
        self.conn.commit()
        return replaced


def run(
    conn: psycopg.Connection[Any],
    *,
    since: date | None = None,
    scope: str | None = None,
    commerce_schema: str = COMMERCE_SCHEMA,
    youtube_schema: str = YOUTUBE_SCHEMA,
) -> StageResult:
    stage = PolarityStage(conn)
    with conn.cursor() as cur:
        cur.execute(
            RUN_START,
            (json.dumps(stage.versions(), ensure_ascii=False), f"analyze:polarity:{RulePolarity.version}"),
        )
        row = cur.fetchone()
    run_id = int(row[0]) if row else 0
    conn.commit()

    months = units = need_rows = wish_rows = replaced = fallbacks = 0
    if _exists(conn, commerce_schema, "review"):
        categories, names = _product_facts(conn, commerce_schema)
        table = _table(commerce_schema, "review")
        for month in _months(conn, table, "written_at", "captured_at", since):
            needs: list[NeedMentionRow] = []
            found = _rows(
                conn,
                table,
                ("source", "product_key", "review_key", "rating", "body", "written_at", "captured_at"),
                "written_at",
                "captured_at",
                month,
                since,
            )
            for source, product_key, review_key, rating, body, written_at, captured_at in found:
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
                if scope and lexicon_category != scope:
                    continue
                units += 1
                needs.extend(stage.need_rows(unit, lexicon_category))
            months += 1
            need_rows += len(needs)
            replaced += stage.write("review", month, needs, ())

    if _exists(conn, youtube_schema, "comments"):
        videos = _channels(conn, youtube_schema)
        table = _table(youtube_schema, "comments")
        for month in _months(conn, table, "published_at", "first_seen_at", since):
            needs = []
            wishes: list[WishMentionRow] = []
            found = _rows(
                conn,
                table,
                ("video_id", "comment_id", "text", "like_count", "published_at", "first_seen_at"),
                "published_at",
                "first_seen_at",
                month,
                since,
            )
            for video_id, comment_id, text, like_count, published_at, first_seen_at in found:
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
                if scope:
                    continue
                units += 1
                needs.extend(stage.need_rows(unit, None))
                wish = stage.wish_row(unit)
                if wish is not None:
                    wishes.append(wish)
            months += 1
            need_rows += len(needs)
            wish_rows += len(wishes)
            replaced += stage.write("yt_comment", month, needs, wishes)

    result = StageResult(
        run_id=run_id,
        months=months,
        units=units,
        need_rows=need_rows,
        wish_rows=wish_rows,
        replaced=replaced,
        captured_at_fallbacks=fallbacks,
    )
    with conn.cursor() as cur:
        cur.execute(RUN_END, (result.note, run_id))
    conn.commit()
    return result
