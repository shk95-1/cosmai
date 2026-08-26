"""청크 색인이 가른 두 팔 → 홀드아웃 대조 (포크 #51).

**이 파이프라인은 아무것도 쓰지 않는다.** 이 답의 행은 (팔, 주제)가 키인데 `팔` 의 경계가 청크 색인이라
`cosmai retrieval chunk` 가 한 번 돌 때마다 움직인다 -- 오늘의 홀드아웃이 내일의 기존이다
(`contracts/interfaces.md` §홀드아웃). 그래서 산출은 표가 아니라 답이고, 읽기 전용이라 운영 DB 에 그대로
돌린다.

모집단은 §대조 의 술어 그대로다(`crosscheck.pipeline.sun_params`) -- 두 팔이 같은 술어 위에 서야 차이가
표본의 것이지 필터의 것이 아니다.

**네 읽기(청크 명부 · 리뷰 키 명부 · 빈 본문 수 · 모집단)는 한 트랜잭션 스냅샷 안에서 한다.** 수집기와
청커는 계속 도는 중이라 밖에 두면 그 넷이 서로 다른 모집단을 가리키고, 그때 `seen + holdout + empty` 는
어떤 모집단의 크기도 아니다. ydc 가 손으로 얹은 정지·전순서 정렬·행수 대조 셋이 여기서 각각 어디로
가는지는 계약의 표가 든다 -- 그대로 옮기면 이 자리에서는 항등식이라 검사가 아니다.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, LiteralString

import psycopg
from psycopg import IsolationLevel

from analysis import holdout
from analysis.crosscheck import COMMERCE_REVIEW
from analysis.crosscheck.pipeline import COMMERCE_SCHEMA, SUN_JOIN, commerce_sql, sun_params
from analysis.holdout import Comparison
from analysis.retrieval import corpus
from analysis.retrieval import topics as topic_registry

# 커머스 청크의 `doc_id` 가 곧 **분석이 실제로 본 리뷰의 명부**다. 이것이 우리 컷오프이고, 날짜가 아닌
# 이유는 계약 §홀드아웃 이 든다 -- `captured_at` 은 수집 시각이지 우리가 본 시각이 아니다.
CHUNKED: LiteralString = "SELECT DISTINCT doc_id FROM retrieval_chunk WHERE source = %s"
# 원천에 없는 커머스 청크를 찾기 위한 명부. 청크에는 외래키가 없다(020) -- 그래서 이 대조가 필요하다.
ALL_KEYS = "SELECT source, review_key FROM {review}"
# 본문이 빈 리뷰는 두 팔 모두에서 뺀다: 빈 본문은 청크를 만들지 않으므로, 남기면 "안 본 리뷰" 가 아니라
# "볼 것이 없는 리뷰" 가 홀드아웃을 채운다 (계약 §홀드아웃).
POPULATION = (
    "SELECT r.source, r.review_key, r.product_key, r.captured_at, r.body FROM {review} r"
    + SUN_JOIN
    + " WHERE coalesce(r.body, '') <> ''"
)
EMPTY = "SELECT count(*) FROM {review} r" + SUN_JOIN + " WHERE coalesce(r.body, '') = ''"
SUN_PRODUCTS = (
    "SELECT count(*) FROM (SELECT DISTINCT source, product_key FROM {rank} "
    "WHERE board = %(board)s OR category_name ILIKE ANY(%(category)s)) sun"
)


class NoHoldout(LookupError):
    """되물을 것이 아직 없다. 실패가 아니라 막힘이라 CLI 에서는 blocked(2) 다."""


@dataclass(frozen=True)
class Built:
    """홀드아웃 한 벌. 아무것도 쓰지 않으므로 이것이 산출의 전부다."""

    comparison: Comparison
    dropped_empty: int = 0
    orphans: tuple[str, ...] = ()

    @property
    def violations(self) -> tuple[str, ...]:
        # 위반 줄은 **이 산출을 믿지 마라** 하나만 뜻한다. 재현 실패는 여기 실리지 않는다.
        if not self.orphans:
            return ()
        return (
            f"chunk_orphan {len(self.orphans)} commerce chunks have no review row "
            f"(e.g. {self.orphans[0]}) -- the seen arm is not the arm the analysis saw",
        )

    @property
    def status(self) -> str:
        """`ok` = 두 팔이 계산됐다. **재현 실패는 여기 실리지 않는다** -- 그것이 이 명령이 답하려고
        존재하는 발견이고, 신호는 `verdict` 와 표가 이미 싣는다 (계약 §종료 코드, §대조·§민감도 와 같은
        자리, 같은 문장)."""
        return "ok" if not self.violations else "partial"

    @property
    def note(self) -> str:
        made = self.comparison
        ranked = len(made.ranked)
        tail = f" partial:{len(self.violations)} violations" if self.violations else ""
        return (
            f"trend holdout seen={made.seen.reviews:,} holdout={made.holdout.reviews:,} "
            f"empty={self.dropped_empty:,} topics={len(made.topics)} ranked={ranked} "
            f"reproduced={made.reproduced}/{ranked} share_reproduced={made.share_reproduced}/{ranked} "
            f"verdict={made.verdict} window={made.window} "
            f"basket_shared={made.basket.shared if made.basket else 0}{tail}"
        )


@dataclass(frozen=True)
class Outcome:
    """`crosscheck`·`sensitivity` 와 같은 세 칸(note·status·violations). `lines` 만 이 명령의 것이다."""

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


@contextmanager
def _snapshot(conn: psycopg.Connection[Any]) -> Iterator[None]:
    """네 읽기가 같은 시점을 보게 한다.

    **이 절의 유일한 기계 방어다.** 수집기와 청커는 계속 도는 중이라, 밖에 두면 팔의 크기와 뺀 빈 본문
    수와 고아 청크 수가 서로 다른 모집단의 수가 된다. ydc 는 PostgREST 위에서 이것을 못 가져 정지를
    손으로 흉내 냈다(count=exact → 전순서 정렬 페이징 → 행수 대조) -- 우리 자리에서 그 셋은 항등식이라
    검사가 아니다 (계약 §홀드아웃).
    """
    previous = conn.isolation_level
    conn.rollback()  # 트랜잭션이 열려 있으면 격리 수준을 바꿀 수 없다
    conn.isolation_level = IsolationLevel.REPEATABLE_READ
    try:
        yield
        conn.commit()
    finally:
        conn.rollback()
        conn.isolation_level = previous


@dataclass(frozen=True)
class Read:
    topic_keys: tuple[str, ...]
    seen: tuple[holdout.Review, ...]
    holdout: tuple[holdout.Review, ...]
    dropped_empty: int
    orphans: tuple[str, ...]


def load(
    conn: psycopg.Connection[Any], *, commerce_schema: str | None = None, source: str = COMMERCE_REVIEW
) -> Read:
    """읽는다. `commerce_schema` 의 `None` 대 `""` 규약은 §대조 와 같다."""
    commerce_schema = COMMERCE_SCHEMA if commerce_schema is None else commerce_schema
    dictionary = topic_registry.use_active(conn)
    topic_keys = tuple(entry["topic"] for entry in dictionary.entries if entry["trend_use"])
    where = sun_params()
    with _snapshot(conn), conn.cursor() as cur:
        cur.execute(commerce_sql(commerce_schema, SUN_PRODUCTS), where)
        if not int((cur.fetchone() or (0,))[0]):
            raise NoHoldout(
                "no suncare product in rank_snapshot; run `cosmai collect commerce` -- the ranking is "
                "what decides the commerce population (contracts/interfaces.md §대조)"
            )
        cur.execute(CHUNKED, (source,))
        chunked = {str(row[0]) for row in cur.fetchall()}
        if not chunked:
            raise NoHoldout(
                "needs.retrieval_chunk has no commerce_review chunk; run `cosmai retrieval chunk` -- "
                "the chunk index is what says which reviews the analysis has seen"
            )
        cur.execute(commerce_sql(commerce_schema, ALL_KEYS))
        known = {corpus.review_doc_id(str(src), str(key)) for src, key in cur.fetchall()}
        cur.execute(commerce_sql(commerce_schema, EMPTY), where)
        dropped = int((cur.fetchone() or (0,))[0])
        cur.execute(commerce_sql(commerce_schema, POPULATION), where)
        rows = cur.fetchall()

    seen: list[holdout.Review] = []
    unseen: list[holdout.Review] = []
    for src, key, product, captured_at, body in rows:
        review = holdout.Review(
            platform=str(src),
            product_key=str(product),
            captured_at=captured_at,
            topics=tuple(topic_registry.match_topics(body or "", dictionary=dictionary)),
        )
        (seen if corpus.review_doc_id(str(src), str(key)) in chunked else unseen).append(review)
    if not seen:
        raise NoHoldout(
            "no chunked review inside the suncare population; run `cosmai retrieval chunk` after "
            "`cosmai collect commerce` -- there is no baseline arm to compare against"
        )
    if not unseen:
        raise NoHoldout(
            "every suncare review is already in the chunk index; there is no unseen sample to ask "
            "with -- let `cosmai collect commerce` run before asking again"
        )
    return Read(
        topic_keys=topic_keys,
        seen=tuple(seen),
        holdout=tuple(unseen),
        dropped_empty=dropped,
        orphans=tuple(sorted(chunked - known)),
    )


def build(
    conn: psycopg.Connection[Any], *, commerce_schema: str | None = None, source: str = COMMERCE_REVIEW
) -> Built:
    read = load(conn, commerce_schema=commerce_schema, source=source)
    return Built(
        comparison=holdout.compare(read.seen, read.holdout, read.topic_keys),
        dropped_empty=read.dropped_empty,
        orphans=read.orphans,
    )


def _pad(text: str, width: int, *, right: bool = False) -> str:
    """한글은 터미널에서 두 칸을 먹는다 (§대조 의 `render` 와 같은 자, 같은 이유)."""
    space = " " * max(0, width - _width(text))
    return (space + text) if right else (text + space)


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def _when(stamp: Any) -> str:
    return stamp.strftime("%Y-%m-%d %H:%M") if stamp else "-"


def render(built: Built) -> list[str]:
    """사람이 읽는 답. **분모는 열마다 머리글이 적는다** -- 적지 않으면 두 축이 섞여 읽힌다."""
    made = built.comparison
    seen, hold = made.seen, made.holdout
    lines = [
        f"팔    기존 {seen.reviews:,}리뷰 · 제품 {len(seen.products)} · "
        f"{_when(seen.first_captured)} ~ {_when(seen.last_captured)}",
        f"      홀드 {hold.reviews:,}리뷰 · 제품 {len(hold.products)} · "
        f"{_when(hold.first_captured)} ~ {_when(hold.last_captured)} · {made.window} · "
        f"본문 빈 리뷰 {built.dropped_empty:,}건은 두 팔 모두에서 뺐다",
        f"지표  언급률 분모 = 그 팔의 리뷰 수 · 구성비 분모 = 그 팔의 주제 언급 합 "
        f"({seen.mentions:,} · {hold.mentions:,}) · 판정 {made.verdict}",
        "  "
        + _pad("", 18)
        + "".join(
            _pad(name, 9, right=True) for name in ("기존률", "홀드률", "Δ%p", "기존비", "홀드비", "순위")
        ),
    ]
    for row in made.topics:
        place = "-" if row.seen_rank is None else f"{row.seen_rank}→{row.holdout_rank}"
        mark = "" if row.seen_rank is None else ("재현" if row.reproduced else "★확인★")
        lines.append(
            f"  {_pad(row.topic_key, 18)}{row.seen_rate:>8.2f}%{row.holdout_rate:>8.2f}%"
            f"{row.rate_diff_pp:>+9.2f}{row.seen_share:>8.2f}%{row.holdout_share:>8.2f}%"
            f"{_pad(place, 9, right=True)}  {mark}"
        )
    lines.append("구성  플랫폼이 바뀌면 수준이 통째로 움직인다. 갈라 세고 기존 구성비로 재가중한다")
    for row in made.platforms:
        lines.append(
            f"  {_pad(row.platform, 18)}기존 {row.seen_reviews:>7,}건 {row.seen_mix:>5.1f}% · "
            f"홀드 {row.holdout_reviews:>7,}건 {row.holdout_mix:>5.1f}%"
        )
    lines.append("      기존 구성비로 표준화 — 구성 효과를 뺀 값")
    for row in made.standardized:
        lines.append(
            f"  {_pad(row.topic_key, 18)}기존 {row.seen_rate:>7.2f}% · 홀드 원값 {row.holdout_rate:>7.2f}% · "
            f"표준화 {row.standardized_rate:>7.2f}% · 남은 차이 {row.residual_pp:>+7.2f}%p"
        )
    basket = made.basket
    lines.append(
        "바스켓 수집 과정이 관측값을 만든다 · "
        + (
            f"기존 {basket.seen_products}제품 · 홀드 {basket.holdout_products}제품 · "
            f"교집합 {basket.shared} (기존 전용 {basket.seen_only} · 홀드 전용 {basket.holdout_only}) · "
            f"교집합 리뷰 기존 {basket.seen_reviews:,} · 홀드 {basket.holdout_reviews:,}"
            if basket
            else "-"
        )
    )
    if not made.basket_rows:
        lines.append("      교집합이 비어 있어 같은 제품으로 다시 셀 수 없다 — 0% 는 답이 아니라 없음이다")
    for row in made.basket_rows:
        lines.append(
            f"  {_pad(row.topic_key, 18)}기존 전체 {row.seen_rate_all:>7.2f}% · "
            f"기존∩ {row.seen_rate_shared:>7.2f}% · 홀드∩ {row.holdout_rate_shared:>7.2f}% · "
            f"∩ 차이 {row.diff_pp:>+7.2f}%p"
        )
    return lines


def run(
    conn: psycopg.Connection[Any], *, commerce_schema: str | None = None, source: str = COMMERCE_REVIEW
) -> Outcome:
    built = build(conn, commerce_schema=commerce_schema, source=source)
    return Outcome(built=built, lines=tuple(render(built)))


__all__ = [
    "ALL_KEYS",
    "CHUNKED",
    "EMPTY",
    "POPULATION",
    "SUN_PRODUCTS",
    "Built",
    "NoHoldout",
    "Outcome",
    "Read",
    "build",
    "load",
    "render",
    "run",
]
