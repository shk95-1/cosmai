"""Canonical records. Every source produces these; nothing here knows a site.

origin: service/trend-radar/src/trend_radar/models.py -- ported for #7 (table shapes, natural keys and
Dataset members are unchanged; contracts/ddl/current/app.trend_radar.sql is the completion bar).

`captured_at` is the run's hour bucket, not the wall clock at parse time -- that is what makes a re-run
of the same hour a no-op instead of a duplicate row. Every `natural_key` below is also the primary key of
the table it lands in.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, field_validator


def hour_bucket(t: datetime) -> datetime:
    """Truncate an instant to the UTC hour it falls in; refuses naive input rather than guessing a zone."""
    if t.tzinfo is None:
        raise ValueError("naive datetime; every instant in this project is UTC-aware")
    return t.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


class Dataset(StrEnum):
    """What a run goes out and collects -- one per `--dataset`. Not a list of record types: PriceRecord
    is derived from a ranking response and has no member here, because no run ever asks a site for one.

    REVIEW_LOW is the same record types as REVIEW (bodies and the stats) from a different walk: one
    board, its low-rated end read to exhaustion rather than sampled. A member of its own because a run
    collects one dataset and its scope is recorded under that name.
    """

    RANKING = "ranking"
    PRODUCT = "product"
    REVIEW = "review"
    REVIEW_LOW = "review_low"
    REVIEW_STATS = "review_stats"
    NEW_PRODUCT = "new_product"


class BaseRecord(BaseModel):
    # extra="forbid": a parser that invents a field name is writing into a void, and the run still
    # looks green without this.
    model_config = ConfigDict(frozen=True, extra="forbid")

    NATURAL_KEY: ClassVar[tuple[str, ...]] = ()

    source: str
    captured_at: datetime

    @field_validator("captured_at")
    @classmethod
    def _must_be_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("captured_at must be timezone-aware (UTC)")
        return v

    def natural_key(self) -> tuple[object, ...]:
        if not self.NATURAL_KEY:
            raise NotImplementedError(f"{type(self).__name__} declares no NATURAL_KEY")
        return tuple(getattr(self, name) for name in self.NATURAL_KEY)


class RankRecord(BaseRecord):
    NATURAL_KEY: ClassVar[tuple[str, ...]] = (
        "source",
        "board",
        "category_key",
        "product_key",
        "captured_at",
    )

    board: str
    category_key: str
    category_name: str | None = None
    rank: int
    product_key: str
    product_name: str
    brand: str | None = None
    price: int | None = None
    discount_rate: int | None = None
    review_count: int | None = None
    review_rating: float | None = None
    rank_delta: int | None = None
    is_new: bool | None = None

    def to_product(
        self,
        volume: str | None = None,
        url: str | None = None,
        ingredients: str | None = None,
    ) -> ProductRecord:
        """The product this row is about, from data the ranking response already carries."""
        return ProductRecord(
            source=self.source,
            captured_at=self.captured_at,
            product_key=self.product_key,
            name=self.product_name,
            brand=self.brand,
            volume=volume,
            url=url,
            ingredients=ingredients,
        )

    def to_price(self) -> PriceRecord | None:
        """This hour's price, or nothing: a missing price is not a free product."""
        if self.price is None:
            return None
        return PriceRecord(
            source=self.source,
            captured_at=self.captured_at,
            product_key=self.product_key,
            price=self.price,
            discount_rate=self.discount_rate,
        )

    def records(self) -> tuple[Record, ...]:
        price = self.to_price()
        product = self.to_product()
        return (self, product) if price is None else (self, product, price)


class ProductRecord(BaseRecord):
    """A product as it stood when it was seen."""

    NATURAL_KEY: ClassVar[tuple[str, ...]] = ("source", "product_key")

    product_key: str
    name: str
    brand: str | None = None
    volume: str | None = None
    url: str | None = None
    ingredients: str | None = None


class PriceRecord(BaseRecord):
    NATURAL_KEY: ClassVar[tuple[str, ...]] = ("source", "product_key", "captured_at")

    product_key: str
    price: int
    discount_rate: int | None = None


class ReviewRecord(BaseRecord):
    NATURAL_KEY: ClassVar[tuple[str, ...]] = ("source", "review_key")

    product_key: str
    review_key: str
    rating: float | None = None
    body: str | None = None
    # A hash of the site's own author id -- never the id or nickname; enough to tell same-author from
    # different-author without keeping a thread back to a person.
    author_hash: str | None = None
    written_at: datetime | None = None


class ReviewStatsRecord(BaseRecord):
    """How many reviews a product has, how good, and how spread out."""

    NATURAL_KEY: ClassVar[tuple[str, ...]] = ("source", "product_key", "captured_at")

    product_key: str
    review_count: int | None = None
    rating_average: float | None = None
    pct_5: int | None = None
    pct_4: int | None = None
    pct_3: int | None = None
    pct_2: int | None = None
    pct_1: int | None = None
    # The only true sentiment split in this project -- a real ratio the site computed, not a ranking
    # that only ever surfaces praise.
    positive_pct: float | None = None
    negative_pct: float | None = None


class ReviewSummaryRecord(BaseRecord):
    """Prose the *site* wrote about a product's reviews -- model-written, not a reviewer's words, so it
    gets its own table rather than folding into review_topic."""

    NATURAL_KEY: ClassVar[tuple[str, ...]] = ("source", "product_key", "rank", "captured_at")

    product_key: str
    rank: int
    title: str
    body: str | None = None


class ReviewAnswerRecord(BaseRecord):
    """One reviewer's own survey answer, attached to their review -- not aggregated, so "are the
    one-star reviews all from 건성 skin" is answerable, not just "what share of reviewers are 건성"."""

    NATURAL_KEY: ClassVar[tuple[str, ...]] = ("source", "review_key", "question_key")

    review_key: str
    product_key: str
    question_key: str
    question_name: str | None = None
    answer: str | None = None


class ReviewTopicRecord(BaseRecord):
    """What many reviews agreed on about a product, at one hour -- an aggregate the site computed, not
    a review itself."""

    NATURAL_KEY: ClassVar[tuple[str, ...]] = ("source", "product_key", "topic_key", "captured_at")

    product_key: str
    topic_key: str
    topic_name: str
    topic_group: str | None = None
    sentence: str | None = None
    is_positive: bool | None = None
    score: float | None = None
    share_pct: int | None = None
    review_count: int | None = None
    rank: int | None = None


class NewProductRecord(BaseRecord):
    """A product a site published on its own new-arrivals page -- distinct from rank_snapshot.is_new
    (a merchandising flag) and from product.first_seen_at (our own first sighting)."""

    NATURAL_KEY: ClassVar[tuple[str, ...]] = ("source", "product_key")

    product_key: str
    name: str
    brand: str | None = None
    listed_at: datetime | None = None

    def to_product(
        self,
        volume: str | None = None,
        url: str | None = None,
        ingredients: str | None = None,
    ) -> ProductRecord:
        return ProductRecord(
            source=self.source,
            captured_at=self.captured_at,
            product_key=self.product_key,
            name=self.name,
            brand=self.brand,
            volume=volume,
            url=url,
            ingredients=ingredients,
        )

    def records(
        self,
        volume: str | None = None,
        url: str | None = None,
        ingredients: str | None = None,
    ) -> tuple[Record, ...]:
        return (self, self.to_product(volume=volume, url=url, ingredients=ingredients))


Record = (
    RankRecord
    | ProductRecord
    | PriceRecord
    | ReviewRecord
    | ReviewTopicRecord
    | ReviewAnswerRecord
    | ReviewStatsRecord
    | ReviewSummaryRecord
    | NewProductRecord
)
