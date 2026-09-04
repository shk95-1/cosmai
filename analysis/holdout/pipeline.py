"""The two arms split by the chunk index -> the holdout comparison (fork #51).

**This pipeline writes nothing.** A row of this answer is keyed by (arm, topic), and the boundary of `arm`
is the chunk index, so it moves every time `cosmai retrieval chunk` runs -- today's holdout is tomorrow's seen
(`contracts/interfaces.md` §Holdout). So the output is an answer rather than a table, and being read-only it
is run against the production DB as it is.

The population is the predicate of §Crosscheck as it is (`crosscheck.pipeline.sun_params`) -- the two arms
have to stand on the same predicate for the difference to be the sample's rather than the filter's.

**The four reads (the chunk roster · the review-key roster · the empty-body count · the population) are done
inside one transaction snapshot.** The collectors and the chunker keep running, so left outside it the four
point at different populations and `seen + holdout + empty` is then the size of no population at all. Where
the three things ydc added by hand (a freeze, a total-order sort, a row-count comparison) each go here is in
the table of the contract -- carried over as they were, they are identities here and not checks.
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
from analysis.crosscheck.pipeline import COMMERCE_SCHEMA, SUN_JOIN, SUN_PRODUCTS, commerce_sql, sun_params
from analysis.holdout import Comparison
from analysis.retrieval import corpus
from analysis.retrieval import topics as topic_registry

# A commerce chunk's `doc_id` is exactly **the roster of reviews the analysis actually saw**. This is our
# cutoff, and why it is not a date is carried by the contract's §Holdout -- `captured_at` is the collection
# time, not the time we saw it.
CHUNKED: LiteralString = "SELECT DISTINCT doc_id FROM retrieval_chunk WHERE source = %s"
# The roster for finding commerce chunks that are not in the source. A chunk has no foreign key (020) --
# which is why this comparison is needed.
ALL_KEYS = "SELECT source, review_key FROM {review}"
# A review with an empty body is removed from both arms: an empty body makes no chunk, so leaving it in
# fills the holdout with "reviews with nothing to see" rather than "reviews never seen" (the contract's
# §Holdout).
POPULATION = (
    "SELECT r.source, r.review_key, r.product_key, r.captured_at, r.body FROM {review} r"
    + SUN_JOIN
    + " WHERE coalesce(r.body, '') <> ''"
)
EMPTY = "SELECT count(*) FROM {review} r" + SUN_JOIN + " WHERE coalesce(r.body, '') = ''"


class NoHoldout(LookupError):
    """There is nothing to ask back yet. Blocked rather than a failure, so in the CLI it is blocked(2)."""


@dataclass(frozen=True)
class Built:
    """One holdout set. Nothing is written, so this is the whole output."""

    comparison: Comparison
    dropped_empty: int = 0
    orphans: tuple[str, ...] = ()

    @property
    def violations(self) -> tuple[str, ...]:
        # A violation line means only **do not trust this output**. A reproduction failure is not carried
        # here.
        if not self.orphans:
            return ()
        return (
            f"chunk_orphan {len(self.orphans)} commerce chunks have no review row "
            f"(e.g. {self.orphans[0]}) -- the seen arm is not the arm the analysis saw",
        )

    @property
    def status(self) -> str:
        """`ok` = the two arms were computed. **A failure to reproduce is not carried here** -- that is the
        finding this command exists to give, and the signal is already carried by `verdict` and the tables
        (the contract's §exit codes, the same place and the same sentence as §Crosscheck and §Sensitivity)."""
        return "ok" if not self.violations else "partial"

    @property
    def note(self) -> str:
        made = self.comparison
        ranked = len(made.ranked)
        tail = f" partial:{len(self.violations)} violations" if self.violations else ""
        return (
            f"trend holdout seen={made.seen.reviews:,} holdout={made.holdout.reviews:,} "
            f"empty={self.dropped_empty:,} topics={len(made.topics)} ranked={ranked} "
            f"reproduced={made.reproduced}/{ranked} "
            # The reproduction count on the composition axis is not carried -- quoted, it reads as
            # independent evidence for the verdict axis, when that difference is a scale rather than
            # stability. Instead the scale itself (the two arms' coefficients) is carried (the contract's
            # §Holdout).
            f"scale={made.seen.scale:.2f}→{made.holdout.scale:.2f} "
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
    """Makes the four reads see the same moment.

    **이 절의 유일한 기계 방어다.** 수집기와 청커는 계속 도는 중이라, 밖에 두면 팔의 크기와 뺀 빈 본문
    count and the orphan chunk count would be counts of different populations. ydc could not have this on
    PostgREST and imitated the stop by hand (count=exact -> total-order paging -> row-count comparison) --
    in our place those three are an identity rather than a check (the contract's §Holdout).
    """
    previous = conn.isolation_level
    conn.rollback()  # the isolation level cannot be changed with a transaction open
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
    """Reads. The `None` versus `""` convention of `commerce_schema` is the same as §Crosscheck.

    **Topic matching runs outside the transaction.** The four queries run one after another and end, and the
    dictionary is applied only after every body has been received -- matching with a cursor open runs into
    `needs_runtime`'s `idle_in_transaction_session_timeout` (15 seconds) (the place #6 and #7 each stepped
    on). The population is a few thousand suncare reviews rather than all the chunks of §Crosscheck, so no
    paging is needed either.
    """
    commerce_schema = COMMERCE_SCHEMA if commerce_schema is None else commerce_schema
    dictionary = topic_registry.use_active(conn)
    topic_keys = tuple(entry["topic"] for entry in dictionary.entries if entry["trend_use"])
    where = sun_params()
    with _snapshot(conn), conn.cursor() as cur:
        cur.execute(commerce_sql(commerce_schema, SUN_PRODUCTS), where)
        if not int((cur.fetchone() or (0,))[0]):
            raise NoHoldout(
                "no suncare product in rank_snapshot; run `cosmai collect commerce` -- the ranking is "
                "what decides the commerce population (contracts/interfaces.md §Crosscheck)"
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
    """Hangul takes two columns in a terminal (the same place and the same reason as `render` in
    §Crosscheck)."""
    space = " " * max(0, width - _width(text))
    return (space + text) if right else (text + space)


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def _when(stamp: Any) -> str:
    return stamp.strftime("%Y-%m-%d %H:%M") if stamp else "-"


def render(built: Built) -> list[str]:
    """The answer a person reads. **The denominator is written in each column heading** -- unwritten, the two
    axes read as mixed."""
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
        # A coefficient above 1 loosens the threshold on the composition axis and below 1 tightens it --
        # the coefficient is printed rather than asserting which, because it differs per population
        # (the contract's §Holdout).
        f"      **판정과 재현 표시는 언급률 축에서만 한다** — 구성비는 언급률을 그 팔의 리뷰당 언급 수"
        f"(기존 {seen.scale:.4f} · 홀드 {hold.scale:.4f})로 나눈 값이라,",
        "      같은 %p 가 두 축에서 같은 것을 뜻하지 않는다. 두 축의 Δ 를 빼거나 재현 수를 비교하지 않는다",
        "  "
        + _pad("", 18)
        + "".join(
            _pad(name, 9, right=True)
            for name in ("기존률", "홀드률", "Δ률%p", "기존비", "홀드비", "Δ비%p", "순위")
        ),
    ]
    for row in made.topics:
        place = "-" if row.seen_rank is None else f"{row.seen_rank}→{row.holdout_rank}"
        mark = "" if row.seen_rank is None else ("재현" if row.reproduced else "★확인★")
        lines.append(
            f"  {_pad(row.topic_key, 18)}{row.seen_rate:>8.2f}%{row.holdout_rate:>8.2f}%"
            f"{row.rate_diff_pp:>+9.2f}{row.seen_share:>8.2f}%{row.holdout_share:>8.2f}%"
            f"{row.share_diff_pp:>+9.2f}{_pad(place, 9, right=True)}  {mark}"
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
    "Built",
    "NoHoldout",
    "Outcome",
    "Read",
    "build",
    "load",
    "render",
    "run",
]
