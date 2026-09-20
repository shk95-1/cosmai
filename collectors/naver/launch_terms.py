"""The term list of the launch-onset axis (#285) -- the file, and the rules that propose what goes
in it.

**The term list is the axis.** Round one of the 2026-09-20 experiment asked DataLab about a brand
plus a category word and the signal disappeared: the series answered for the brand's whole shelf, so
its onset was the brand's, not the product's. Round two used product-line-specific terms and the
onset landed 1-3 months after the MFDS filing on the cleanly named lines. So a product whose only
expressible terms are a brand and a category word is **refused** rather than asked about -- a series
that means nothing is worse than no series, because it still produces a claim.

`launch_terms.json` is the reviewed result: `{product_ref: [term, ...]}`. It is reviewed data and
not a derivation, because nothing here can tell a line name from a marketing adjective; what is
derived is the *candidate*, by `tool/derive-naver-launch-terms`, and a person keeps or drops it. The
file ships empty on purpose (#285): inventing product terms would put fabricated evidence in the
ledger under an axis whose whole job is to be checkable.

The rules below are pure and take the catalogue as an argument, so they can be measured against a
fixture and against production without either one being the definition:

  generic_tokens    a token of `name_norm` that appears in the products of at least
                    `LAUNCH_GENERIC_MIN_BRANDS` distinct brands is a category word. Derived rather
                    than listed, so it keeps holding as the catalogue grows.
  line_tokens       what is left of `name_norm` after the generic words and the brand -- the name of
                    the line, which is the only part that can identify one product.
  candidate_terms   the three shapes the experiment actually used: brand+line+category,
                    line+category, brand+line. Empty when there is no line token: that is the
                    refusal.
  match_strength    `exact` when every term of the product is **specific** to it -- no other
                    `product_ref` of the same brand has a name holding all of that term's tokens --
                    and `partial` otherwise.

**What that word costs, under `rule-v1.1`.** This axis emits only `not_after`, so row 6 (a tier held
up by a lower bound alone) never reads its strength. Row 3 does: `not_new` is decided on
`latest_exact`, the tightest upper bound **among the `exact` claims**, so a `partial` onset can no
longer declare a product old on its own. It still reaches row 2 `conflict` against a lower bound,
which is right -- a crossing is the MFDS cross-check this axis is kept for, and two of the seven
measured sunscreens were exactly that. So a `partial` claimed too generously buys a wrong `not_new`,
and that is the reason the rule here is the strict one: a term whose tokens a same-brand sibling's
name also holds answers for the pair, and the series then rises when the **older** sibling launched.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import NamedTuple

from collectors.naver.scope import LAUNCH_GENERIC_MIN_BRANDS, LAUNCH_MAX_TERMS_PER_PRODUCT

TERMS_PATH = Path(__file__).resolve().parent / "launch_terms.json"


class Ref(NamedTuple):
    """One `needs.product_ref` row, only the three columns the rules read."""

    product_ref: str
    brand: str | None
    name_norm: str


def tokens(value: str | None) -> tuple[str, ...]:
    """Whitespace tokens, in the order the name holds them. `name_norm` is already normalised by
    `analysis/linker` (parentheses, volume and promo wording removed), so nothing more is stripped
    here -- a second normalisation would be a second definition of a product's name."""
    return tuple(t for t in (value or "").split() if t)


def generic_tokens(
    refs: Iterable[Ref], *, extra: Iterable[str] = (), min_brands: int = LAUNCH_GENERIC_MIN_BRANDS
) -> frozenset[str]:
    """The category words of this catalogue: a token carried by the products of `min_brands` or more
    distinct brands. Distinct **brands**, not products: a brand that repeats its own name in every
    one of its product names would otherwise turn that name into a category word, and then every
    one of its products would be refused."""
    brands_of: dict[str, set[str]] = {}
    for ref in refs:
        brand = (ref.brand or "").strip()
        for token in tokens(ref.name_norm):
            brands_of.setdefault(token, set()).add(brand)
    frequent = {token for token, brands in brands_of.items() if len(brands) >= min_brands}
    return frozenset(frequent | {t for t in extra if t})


def line_tokens(ref: Ref, generic: frozenset[str]) -> tuple[str, ...]:
    """`name_norm` minus the generic words and minus the brand's own tokens, in name order."""
    brand = set(tokens(ref.brand))
    return tuple(t for t in tokens(ref.name_norm) if t not in generic and t not in brand)


def category_token(ref: Ref, generic: frozenset[str]) -> str | None:
    """The generic word a term is allowed to carry: the **last** one in the name. Korean product
    names put the category word at the end (`<brand> <line> <category>`), and the experiment's terms
    each ended in one."""
    found = [t for t in tokens(ref.name_norm) if t in generic]
    return found[-1] if found else None


def candidate_terms(ref: Ref, generic: frozenset[str]) -> tuple[str, ...]:
    """The terms to propose for this product, or `()` -- the refusal. The three shapes are the
    experiment's own; the cap is `LAUNCH_MAX_TERMS_PER_PRODUCT`."""
    line = " ".join(line_tokens(ref, generic))
    if not line:
        # The refusal. Everything that could be asked about this product is its brand and a category
        # word, and that is the term round one of the experiment measured the signal dying on.
        return ()
    brand = (ref.brand or "").strip()
    category = category_token(ref, generic)
    shapes = [
        f"{brand} {line} {category}" if brand and category else "",
        f"{line} {category}" if category else "",
        f"{brand} {line}" if brand else "",
        line if not brand and not category else "",
    ]
    out: list[str] = []
    for shape in shapes:
        if shape and shape not in out:
            out.append(shape)
    return tuple(out[:LAUNCH_MAX_TERMS_PER_PRODUCT])


def match_strength(terms: Sequence[str], ref: Ref, refs: Iterable[Ref]) -> str:
    """`exact` when no other `product_ref` **of the same brand** holds every token of every term;
    `partial` otherwise (contracts/interfaces.md §Launch evidence: the join may have taken a
    variant). A product that is the only one of its brand in the catalogue is `exact` by this rule,
    which is honest about what the catalogue can see and no more.

    The gate is the same brand only. A different brand's product is not the variant a join takes --
    that is what the two measured MFDS errors were, a later variant and an older line name of the
    *same* line -- and every term carrying a brand names its own anyway."""
    if not terms:
        return "partial"
    siblings = [
        set(tokens(other.name_norm)) | set(tokens(other.brand))
        for other in refs
        if other.product_ref != ref.product_ref and (other.brand or "") == (ref.brand or "")
    ]
    for term in terms:
        needed = set(tokens(term))
        if any(needed <= sibling for sibling in siblings):
            return "partial"
    return "exact"


def load(path: Path = TERMS_PATH) -> dict[str, tuple[str, ...]]:
    """The reviewed file: `{product_ref: terms}`. A product with an empty list is refused the same
    way a product missing from the file is -- both mean "do not ask DataLab about this one"."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    products = raw.get("products") or {}
    out: dict[str, tuple[str, ...]] = {}
    for product_ref, listed in products.items():
        terms = tuple(dict.fromkeys(t.strip() for t in listed if isinstance(t, str) and t.strip()))
        if not terms:
            continue
        if len(terms) > LAUNCH_MAX_TERMS_PER_PRODUCT:
            # Loudly, not by truncation: a reviewed file is a decision, and silently dropping the
            # last term of it would send a request nobody reviewed.
            raise ValueError(
                f"{product_ref} lists {len(terms)} terms, over the cap of {LAUNCH_MAX_TERMS_PER_PRODUCT}"
            )
        out[product_ref] = terms
    return out


def generic_extra(path: Path = TERMS_PATH) -> frozenset[str]:
    """Category words the frequency rule cannot see yet (a word a single brand happens to own
    today), kept beside the reviewed terms so one file holds the whole decision."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return frozenset(t.strip() for t in (raw.get("generic") or []) if isinstance(t, str) and t.strip())


__all__ = [
    "TERMS_PATH",
    "Ref",
    "tokens",
    "generic_tokens",
    "line_tokens",
    "category_token",
    "candidate_terms",
    "match_strength",
    "load",
    "generic_extra",
    "LAUNCH_MAX_TERMS_PER_PRODUCT",
]
