"""The brand_link and product_match evaluation implementations. `analysis.registry.load_implementations()`
plugs them in."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from analysis.lexicon import load_lexicon
from analysis.linker import LINKER_VERSION, RuleLinker, accepts, normalized
from analysis.registry import LabeledRow, register
from analysis.types import Lexicon, TextUnit

# labeled_set.ref is '<sample>:<src>/<ref_id>/<brand>', so the brand is the last piece (formats.md).
BRAND_FROM_REF = 2
# labeled_set.text is '<src_a>:<name_a> | <src_b>:<name_b>' (db/seed/labeled.py). It is not in extra (T8).
PAIR_SEPARATOR = " | "


@dataclass(frozen=True, eq=False)
class BrandLinkPredictor:
    """Per row it answers "does our circuit link that brand in that context" -- OK is accepted, FP is not."""

    lexicon: Lexicon | None = None
    linker: RuleLinker = field(default_factory=RuleLinker)

    def _lexicon(self) -> Lexicon:
        if self.lexicon is not None:
            return self.lexicon
        # The Predictor protocol does not hand over a connection -- the implementation opens the connection
        # that reads the dictionary. Its destination is not held per predictor but comes from the one place,
        # analysis.predictors: a url field of its own here left `cosmai eval --url` out of reach and this
        # predictor alone read the dictionary of the production DB.
        from analysis.predictors import connect_lexicon

        with connect_lexicon() as conn:
            return load_lexicon(conn)

    def __call__(self, rows: Sequence[LabeledRow]) -> Sequence[str]:
        lexicon = self._lexicon()
        out: list[str] = []
        for row in rows:
            brand = row.ref.rsplit("/", BRAND_FROM_REF)[-1]
            unit = TextUnit(
                src="yt_comment",
                site="youtube",
                ref=row.ref,
                text=row.text,
                observed_at=date(1970, 1, 1),
                observed_at_resolution="day",
            )
            # (d) surface_re bites ingredient surfaces too. Measuring the precision of the brand circuit
            # means filtering by kind.
            linked = {h.canonical for h in self.linker.link(unit, lexicon) if h.kind == "brand"}
            out.append("OK" if brand in linked else "FP")
        return out


@dataclass(frozen=True, eq=False)
class ProductMatchPredictor:
    """Per pair it answers accepted (Y) or not (N). The score is the precision over the accepted set
    (interfaces.md)."""

    linker: RuleLinker = field(default_factory=RuleLinker)

    def __call__(self, rows: Sequence[LabeledRow]) -> Sequence[str]:
        out: list[str] = []
        for row in rows:
            left, _, right = row.text.partition(PAIR_SEPARATOR)
            src_a, _, name_a = left.partition(":")
            src_b, _, name_b = right.partition(":")
            # The evaluation rows have no brand column -- candidate generation has already grouped the pair
            # by brand, so it is judged by the names alone.
            a = normalized(name_a, "", src_a)
            b = normalized(name_b, "", src_b)
            out.append("Y" if accepts(a, b).ok else "N")
        return out


def register_implementations() -> None:
    """Only registry.load_implementations() causes registration (#99)."""
    register("brand_link", LINKER_VERSION, BrandLinkPredictor())
    register("product_match", LINKER_VERSION, ProductMatchPredictor())
