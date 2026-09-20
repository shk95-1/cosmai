"""The first-round launch-evidence axes (#283) — `contracts/interfaces.md` §Launch evidence is canonical.

This module knows no database. It turns rows of the source tables into `LaunchClaimRow`s and decides
two things per claim: **which direction** the observation bounds the launch in, and **how sure the
join to the product is** (`exact` · `partial`), which the rule table reads — a tier held up by a
lower bound alone is given only on an `exact` join (#282 rule table row 6).

Four axes, and what each may honestly say:

  mfds_report       `not_before`, day. A cosmetic is filed before it is sold, so the report date is a
                    real lower bound. The join is a name join and it is the one that can be wrong.
  vendor_board      `not_after`, day. A product on a vendor's NEW board was **on sale** on the day we
                    captured the board, so the launch is no later. The board does **not** bound the
                    launch from below: a vendor may list a product it has carried for months, and
                    `listed_at` is NULL on all 487 rows (#282 facts), so we hold no date the vendor
                    itself calls the listing date.
  old_review        `not_after`, day. The product existed when the earliest review we hold was
                    written. It can only prove age, never newness.
  vendor_title_tag  `not_after`, day, and **excluded from the verdict** by rule version 1
                    (`analysis.launch.EXCLUDED_AXES`): marketing text on one vendor. It is recorded
                    so the exclusion stays a decision that can be re-measured rather than an absence.

The vendor tag vocabulary is data and lives in `new_tags.csv` beside this file, not in the source.
"""

from __future__ import annotations

import csv
import functools
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from analysis.linker import normalize_name
from analysis.types import LaunchClaimRow
from db.seed.mfds import fold, normalize_company

# The axis names. `vendor_title_tag` is the one `analysis.launch.EXCLUDED_AXES` and the view's own
# literal already name, so the exclusion needs no new machinery here -- an axis is excluded by its
# name and by nothing else, which is what makes turning it back on a rule-version change.
MFDS = "mfds_report"
VENDOR_BOARD = "vendor_board"
OLD_REVIEW = "old_review"
VENDOR_TITLE_TAG = "vendor_title_tag"
AXES = (MFDS, VENDOR_BOARD, OLD_REVIEW, VENDOR_TITLE_TAG)

# `contracts/versioning.md`'s `rule-vX.Y`: the version of these axis rules, stored on every claim.
AXIS_VERSION = "rule-v1.0"

NOT_BEFORE = "not_before"
NOT_AFTER = "not_after"
DAY = "day"
EXACT = "exact"
PARTIAL = "partial"

# The role `analysis/linker` gives the row the ref was minted from (`_ref_id(anchor)`).
PRIMARY = "primary"

# A line key shorter than this is not evidence of anything: once the brand and the noise come off, a
# two- or three-character remainder is contained in a good part of a 4,735-row ledger.
MIN_LINE_KEY = 4

TAGS_CSV = Path(__file__).with_name("new_tags.csv")
# A vendor tag is a bracketed segment of the listing title. A bare word in the middle of a name is
# the product's own name, not the vendor calling it new.
BRACKETED = re.compile(r"\[([^\]]*)\]|\(([^)]*)\)|【([^】]*)】")


@dataclass(frozen=True)
class Registration:
    """One row of `needs.mfds_registration`, as this axis needs it."""

    report_seq: str
    item_name: str
    entp_key: str
    report_date: date


@dataclass(frozen=True)
class CatalogueRef:
    """One row of `needs.product_ref`: the subject a claim is about."""

    product_ref: str
    brand: str | None
    name_norm: str


@dataclass(frozen=True)
class Listing:
    """One row of `needs.product_member`: a site listing folded onto a canonical product."""

    source: str
    product_key: str
    product_ref: str
    role: str


@dataclass(frozen=True)
class BrandIndex:
    """The brand lexicon folded the way `db/seed/mfds.py` folds a company name, so that a
    registration's stored `entp_key`, a lexicon surface and a catalogue brand are one kind of key.

    Using that folding rather than `analysis.linker.normalize_brand` is what lets the two sides meet
    at all: `entp_key` is a **stored** column computed by `normalize_company`, and a second folding
    in this module would be the drift 028 was written to avoid.
    """

    canonical_of: Mapping[str, str]
    keys_of: Mapping[str, frozenset[str]]

    @classmethod
    def of(cls, surfaces: Iterable[tuple[str, str]]) -> BrandIndex:
        canonical_of: dict[str, str] = {}
        keys: dict[str, set[str]] = {}
        for canonical, surface in surfaces:
            bucket = keys.setdefault(canonical, set())
            for spelling in (canonical, surface):
                key = normalize_company(spelling)
                if key:
                    canonical_of.setdefault(key, canonical)
                    bucket.add(key)
        return cls(canonical_of, {name: frozenset(found) for name, found in keys.items()})

    def keys_for(self, brand: str | None) -> frozenset[str]:
        """Every folded spelling of the brand this catalogue row carries -- its own, plus the other
        surfaces the lexicon files under the same canonical (the alias case)."""
        key = normalize_company(brand)
        if not key:
            return frozenset()
        canonical = self.canonical_of.get(key)
        return frozenset({key}) | self.keys_of.get(canonical or "", frozenset())


def line_key(name: str | None, brand_keys: Iterable[str]) -> str:
    """The comparable core of a product name: what is left once the brand and everything that is not
    the product line come off.

    The normalisation is `analysis.linker.normalize_name`, which is the one the catalogue's own
    `name_norm` was built with -- bracketed vendor tags, volume and count words, SPF/PA specs and the
    promotional vocabulary dropped, formulation synonyms folded. Then `fold` takes the spacing and
    the punctuation, because a registered name is written glued and a listing name is not, and the
    brand's folded spellings are removed last: a registered name carries the brand inside it and a
    listing name carries it in its own column.
    """
    base = fold(normalize_name(name or "", None))
    # Longest first, so a brand whose short spelling is a prefix of its long one does not leave the
    # tail of the long one behind.
    for key in sorted((k for k in brand_keys if k), key=len, reverse=True):
        base = base.replace(key, "")
    return base


def registrations_by_brand(
    registrations: Iterable[Registration], keys_of: Mapping[str, frozenset[str]]
) -> dict[str, list[Registration]]:
    """Canonical brand -> the registrations that could belong to it, scanned once for the whole
    catalogue rather than once per product.

    A registration belongs to a brand when its **registered name** carries one of the brand's folded
    spellings, or when its filer does. Both are needed: `entp_name` is the reporting company and is
    an OEM on most rows (233 of 4,735 resolve to a lexicon brand, measured 2026-09-04), so gating on
    the company alone throws most of the ledger away -- while a registered name is a legal name that
    almost always opens with the brand.
    """
    out: dict[str, list[Registration]] = {canonical: [] for canonical in keys_of}
    wanted = [(canonical, keys) for canonical, keys in keys_of.items() if keys]
    for registration in registrations:
        folded = fold(registration.item_name)
        for canonical, keys in wanted:
            if registration.entp_key in keys or any(key in folded for key in keys):
                out[canonical].append(registration)
    return out


def mfds_claims(
    ref: CatalogueRef,
    registrations: Sequence[Registration],
    brand_keys: Iterable[str],
    observed_at: datetime,
) -> list[LaunchClaimRow]:
    """One `not_before` claim per registration this product matches -- every one of them, because
    the rule table and not the axis picks the bound (#282).

    **`exact` is a full equality**: the brand is established on both sides *and* the two line keys
    are the same string with nothing left over on either. Anything else that still matches -- one
    line key contained in the other -- is `partial`, and a brand-level match with no line-level
    containment at all is **no claim**, since the brand's newest filing would otherwise land under
    every product that brand sells (26 of 27 sun-care refs match at brand level, #125).

    The two join errors the 2026-09-20 experiment found both fall out as `partial` under that
    definition: a later colour/variant filing adds a token, and a line name older than the product
    drops one, so neither is an equality.
    """
    keys = frozenset(key for key in brand_keys if key)
    product = line_key(ref.name_norm, keys)
    if not keys or len(product) < MIN_LINE_KEY:
        return []
    claims: list[LaunchClaimRow] = []
    for registration in registrations:
        folded = fold(registration.item_name)
        if registration.entp_key not in keys and not any(key in folded for key in keys):
            continue
        registered = line_key(registration.item_name, keys)
        if len(registered) < MIN_LINE_KEY:
            continue
        if registered == product:
            strength = EXACT
        elif product in registered or registered in product:
            strength = PARTIAL
        else:
            continue
        claims.append(
            LaunchClaimRow(
                product_ref=ref.product_ref,
                axis=MFDS,
                direction=NOT_BEFORE,
                claimed_on=registration.report_date,
                claimed_precision=DAY,
                # The registration's own key, so a `conflict` traces back to the exact filing.
                source_ref=registration.report_seq,
                match_strength=strength,
                axis_version=AXIS_VERSION,
                observed_at=observed_at,
            )
        )
    return claims


def member_strength(role: str) -> str:
    """How sure a site listing's join to its canonical product is.

    `product_ref` is minted from the cluster's anchor (`analysis/linker._ref_id`), so the anchor row
    **is** that product by construction and nothing can have gone wrong. Every other member reached
    the ref through the linker's cross-site similarity thresholds, which is the same class of join as
    the MFDS containment -- so it is `partial`, and a tier resting on it alone is refused by row 6.
    """
    return EXACT if role == PRIMARY else PARTIAL


def board_claim(listing: Listing, first_captured: date, observed_at: datetime) -> LaunchClaimRow:
    """The vendor new-product board: `not_after` the first capture we hold.

    What the board proves is that the product was **on sale** that day, which bounds the launch from
    above. It does not bound it from below -- a vendor may put a product it has carried for months on
    its NEW board, and `listed_at` is NULL on all 487 rows, so there is no date the vendor itself
    calls the listing date. Claimed as `at`, this axis would mint `new_3m` for every boarded product
    out of a four-week collection window; claimed as `not_before`, it would do the same on marketing
    text. As an upper bound it can only make a product older or corroborate a lower one, which is
    exactly as much as it knows.
    """
    return _site_claim(VENDOR_BOARD, listing, first_captured, observed_at)


def review_claim(listing: Listing, first_written: date, observed_at: datetime) -> LaunchClaimRow:
    """The oldest review we hold: `not_after` the day it was written. It can only prove age -- a
    recent earliest review says nothing, because the collector pages only recent reviews."""
    return _site_claim(OLD_REVIEW, listing, first_written, observed_at)


def title_tag_claim(listing: Listing, first_seen: date, tag: str, observed_at: datetime) -> LaunchClaimRow:
    """The vendor's `[NEW]`-style title tag, recorded and read by no rule of version 1.

    It carries the same upper bound as the board -- the listing existed on the day we first saw it --
    and the tag itself goes in `note`, where no rule may read it. What the tag would add if it were
    trusted is a lower bound, and that is the claim this rule version declines to make: it is
    marketing copy on a single vendor.
    """
    return _site_claim(VENDOR_TITLE_TAG, listing, first_seen, observed_at, note=tag)


def _site_claim(
    axis: str, listing: Listing, claimed_on: date, observed_at: datetime, note: str | None = None
) -> LaunchClaimRow:
    return LaunchClaimRow(
        product_ref=listing.product_ref,
        axis=axis,
        direction=NOT_AFTER,
        claimed_on=claimed_on,
        claimed_precision=DAY,
        source_ref=f"{listing.source}:{listing.product_key}",
        match_strength=member_strength(listing.role),
        axis_version=AXIS_VERSION,
        observed_at=observed_at,
        note=note,
    )


def load_tags(path: Path = TAGS_CSV) -> tuple[str, ...]:
    """The vendor tag vocabulary, in file order -- the first match wins, so the order is the file's."""
    with path.open(encoding="utf-8", newline="") as handle:
        return tuple(row["tag"] for row in csv.DictReader(handle) if row["tag"])


def title_tag(name: str | None, tags: Iterable[str]) -> str | None:
    """The new-product tag a listing title carries, or None.

    Only a bracketed segment counts: 530 of 10,032 titles carry one and they are all vendor
    decoration, while the same word inside the name proper belongs to the product.
    """
    for match in BRACKETED.finditer(name or ""):
        segment = next(group for group in match.groups() if group is not None)
        for tag in tags:
            if _tag_pattern(tag).search(segment):
                return tag
    return None


@functools.cache
def _tag_pattern(tag: str) -> re.Pattern[str]:
    """Latin tags are bounded on both sides and Korean ones on neither -- the same split, and for the
    same reason, as `db/seed/mfds.py`'s corporate forms: unbounded, `NEW` lives inside `Renewal`;
    bounded, a Korean tag would match nothing, because it is written glued to the next word as often
    as not."""
    escaped = re.escape(tag)
    if tag.isascii():
        return re.compile(rf"\b{escaped}\b", re.IGNORECASE)
    return re.compile(escaped)
