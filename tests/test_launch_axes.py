"""The first-round launch-evidence axes (#283): direction, precision and join strength.

Each axis is held to two things here — the **direction** it may claim, and what `exact` means for
its own join — because the rule table of #282 reads both: a lower bound alone yields a tier only on
an `exact` join (row 6), and an upper bound never makes a product new (row 4). Every axis also
carries its measured trap: the MFDS later-variant registration, the 2019 review, and a listing that
reached its canonical product through the linker's fuzzy cross-site match rather than by being the
row the ref was minted from.

No database: this file is the rules alone, and the writer is `tests/test_launch_writer.py`. The
product and company names the cases need are Korean, so they are data under `tests/fixtures/launch/`
rather than literals here (`tool/checks/lang`).
"""

from __future__ import annotations

import csv
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from analysis.launch import EXCLUDED_AXES, launch_interval, launch_verdict
from analysis.launch.axes import (
    AXES,
    AXIS_VERSION,
    EXACT,
    MFDS,
    OLD_REVIEW,
    PARTIAL,
    VENDOR_BOARD,
    VENDOR_TITLE_TAG,
    BrandIndex,
    CatalogueRef,
    Listing,
    Registration,
    board_claim,
    line_key,
    load_tags,
    member_strength,
    mfds_claims,
    registrations_by_brand,
    review_claim,
    title_tag,
    title_tag_claim,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "launch"
AT = datetime(2026, 9, 20, 3, 0, tzinfo=UTC)
REFERENCE = date(2026, 9, 20)


def _rows(name: str) -> list[dict[str, str]]:
    with (FIXTURES / name).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


SURFACES = tuple((r["canonical"], r["surface"]) for r in _rows("brand_surfaces.csv"))
INDEX = BrandIndex.of(SURFACES)
REGISTRATIONS = {
    r["case"]: Registration(
        r["report_seq"], r["item_name"], r["entp_key"], date.fromisoformat(r["report_date"])
    )
    for r in _rows("registrations.csv")
}
CATALOGUE = {
    r["case"]: CatalogueRef(r["product_ref"], r["brand"], r["name_norm"]) for r in _rows("catalogue.csv")
}
TITLES = {r["case"]: (r["title"], r["tag"] or None) for r in _rows("titles.csv")}

SUNSCREEN = CATALOGUE["sunscreen"]
ANCHOR = Listing("oliveyoung", "A1", "oy:A1", "primary")
MEMBER = Listing("daisomall", "D1", "oy:A1", "member")


def _claims(ref: CatalogueRef, cases: tuple[str, ...]) -> list:
    registrations = [REGISTRATIONS[case] for case in cases]
    return mfds_claims(ref, registrations, INDEX.keys_for(ref.brand), AT)


# ---------- axis 1: the MFDS report date ----------


def test_the_mfds_axis_claims_a_lower_bound_at_the_report_date():
    # A cosmetic is filed before it is sold, so the filing date is the one thing the ledger proves:
    # the launch cannot precede it. Never `at` -- the gap between filing and shelf is months.
    base = REGISTRATIONS["base"]
    (claim,) = _claims(SUNSCREEN, ("base",))
    assert (claim.axis, claim.direction, claim.claimed_precision) == (MFDS, "not_before", "day")
    assert claim.claimed_on == base.report_date
    assert claim.source_ref == base.report_seq
    assert claim.axis_version == AXIS_VERSION


def test_a_registration_naming_this_exact_line_and_nothing_more_is_an_exact_join():
    # `exact` is full equality of the normalised line on both sides -- nothing left over on either.
    (claim,) = _claims(SUNSCREEN, ("base",))
    assert claim.match_strength == EXACT


def test_the_later_variant_trap_is_recorded_as_a_partial_claim_naming_its_own_registration():
    # The measured trap of 2026-09-20: a later colour/variant filing whose name contains the older
    # line's. It is kept -- the rule table picks the bound -- but it is `partial`, and it names the
    # registration it matched so a `conflict` can be traced back to that filing.
    variant = REGISTRATIONS["variant"]
    claims = {c.source_ref: c for c in _claims(SUNSCREEN, ("base", "variant"))}
    assert set(claims) == {REGISTRATIONS["base"].report_seq, variant.report_seq}
    assert claims[variant.report_seq].match_strength == PARTIAL
    assert claims[variant.report_seq].claimed_on == variant.report_date


def test_a_variant_that_inserts_its_token_in_the_middle_is_not_reached_at_all():
    # The blind spot of a containment join, pinned rather than left to be discovered: a filing that
    # puts its distinguishing token *inside* the line name is neither an equality nor a containment,
    # so the axis makes no claim about it. That errs the safe way -- no bound rather than a wrong
    # one -- and it is why this axis's coverage is reported per product rather than assumed.
    assert _claims(SUNSCREEN, ("infix_variant",)) == []


def test_a_registration_of_another_line_of_the_same_brand_makes_no_claim():
    # 26 of 27 sun-care refs match at brand level (#125). A brand-level match alone would put the
    # brand's newest filing under every one of its products, so it is not a claim.
    assert _claims(SUNSCREEN, ("other_line",)) == []


def test_volume_set_and_promotional_words_do_not_stop_an_exact_join():
    (claim,) = _claims(CATALOGUE["noisy"], ("base",))
    assert claim.match_strength == EXACT


def test_a_brand_alias_of_the_lexicon_reaches_the_registration():
    (claim,) = _claims(CATALOGUE["latin"], ("base",))
    assert claim.match_strength == EXACT


def test_the_reporting_company_may_be_a_contract_manufacturer():
    # `entp_name` is the filer, often an OEM, so the brand is established by the registered NAME as
    # well as by the company key -- gating on the company alone loses most of the ledger.
    oem = REGISTRATIONS["oem"]
    (claim,) = _claims(CATALOGUE["korean_beauty"], ("oem",))
    assert (claim.match_strength, claim.source_ref) == (EXACT, oem.report_seq)


def test_a_line_key_too_short_to_mean_anything_makes_no_claim():
    assert _claims(CATALOGUE["bare"], ("base",)) == []


def test_registrations_are_grouped_by_the_brand_their_name_or_their_filer_carries():
    own = SUNSCREEN.brand or ""
    other = CATALOGUE["korean_beauty"].brand or ""
    wanted = {brand: INDEX.keys_for(brand) for brand in (own, other)}
    grouped = registrations_by_brand([REGISTRATIONS[c] for c in ("base", "variant", "oem")], wanted)
    assert {r.report_seq for r in grouped[own]} == {
        REGISTRATIONS["base"].report_seq,
        REGISTRATIONS["variant"].report_seq,
    }
    assert {r.report_seq for r in grouped[other]} == {REGISTRATIONS["oem"].report_seq}


def test_the_line_key_drops_the_brand_and_the_noise_from_both_sides():
    keys = INDEX.keys_for(SUNSCREEN.brand)
    assert line_key(CATALOGUE["noisy"].name_norm, keys) == line_key(REGISTRATIONS["base"].item_name, keys)


# ---------- axis 2: the vendor new-product board ----------


def test_the_vendor_board_bounds_the_launch_from_above_and_not_from_below():
    # The board says the product was on sale on the day we captured it, which bounds the launch
    # from above. It does not say the product was not on sale earlier, and `listed_at` is NULL on
    # all 487 rows, so there is no date the vendor itself calls the listing date.
    claim = board_claim(ANCHOR, date(2026, 8, 20), AT)
    assert (claim.axis, claim.direction, claim.claimed_precision) == (VENDOR_BOARD, "not_after", "day")
    assert claim.claimed_on == date(2026, 8, 20)
    assert claim.source_ref == "oliveyoung:A1"


def test_a_board_claim_alone_never_makes_a_product_new():
    # Rule table row 4. This is why the direction matters: forced into `not_before`, every one of
    # the 487 boarded products would be `new_3m` on marketing text alone.
    interval = launch_interval("oy:A1", [board_claim(ANCHOR, date(2026, 8, 20), AT)])
    assert launch_verdict(interval, REFERENCE).verdict == "unknown"


# ---------- axis 3: the oldest held review ----------


def test_the_old_review_axis_proves_age_and_only_age():
    claim = review_claim(ANCHOR, date(2019, 3, 2), AT)
    assert (claim.axis, claim.direction, claim.claimed_precision) == (OLD_REVIEW, "not_after", "day")
    verdict = launch_verdict(launch_interval("oy:A1", [claim]), REFERENCE)
    assert verdict.verdict == "not_new"


def test_a_recent_earliest_review_proves_nothing():
    # 3,392 of 3,511 reviewed products have their earliest held review in 2026 because the collector
    # pages only recent reviews -- an upper bound inside the window is not evidence of newness.
    claim = review_claim(ANCHOR, date(2026, 7, 2), AT)
    assert launch_verdict(launch_interval("oy:A1", [claim]), REFERENCE).verdict == "unknown"


# ---------- the site-level axes' join strength ----------


@pytest.mark.parametrize(("role", "strength"), [("primary", EXACT), ("member", PARTIAL)])
def test_a_site_listing_is_an_exact_join_only_where_the_ref_was_minted_from_it(role, strength):
    # `product_ref` is minted from the cluster's anchor (`analysis/linker._ref_id`), so the anchor
    # row IS the product by construction. Every other member reached it through the linker's
    # cross-site threshold, which is the same class of join the MFDS containment is.
    assert member_strength(role) == strength


def test_the_claims_of_a_fuzzy_member_row_carry_that_weakness():
    assert board_claim(MEMBER, date(2026, 8, 20), AT).match_strength == PARTIAL
    assert review_claim(MEMBER, date(2019, 3, 2), AT).match_strength == PARTIAL


# ---------- axis 4: the vendor title tag ----------


def test_the_title_tag_axis_is_the_one_this_rule_version_excludes():
    assert VENDOR_TITLE_TAG in EXCLUDED_AXES
    assert set(AXES) - EXCLUDED_AXES == {MFDS, VENDOR_BOARD, OLD_REVIEW}


def test_a_recorded_title_tag_is_counted_and_moves_no_bound():
    tag = TITLES["bracketed_latin"][1] or ""
    claim = title_tag_claim(ANCHOR, date(2026, 8, 17), tag, AT)
    interval = launch_interval("oy:A1", [claim])
    assert (interval.claims, interval.excluded_claims) == (0, 1)
    assert (interval.earliest, interval.latest) == (None, None)


@pytest.mark.parametrize("case", sorted(TITLES))
def test_a_tag_is_read_from_a_bracketed_segment_and_not_from_the_product_name(case):
    # One title carries a tag's letters inside the product's own name and another carries `new`
    # inside an English word -- neither of them is the vendor calling the product new.
    title, expected = TITLES[case]
    assert title_tag(title, load_tags()) == expected


def test_the_tag_vocabulary_is_data_beside_the_module():
    tags = load_tags()
    assert "NEW" in tags
    assert len(tags) >= 5


# ---------- the two ends, against the rule table ----------


def test_an_exact_mfds_bound_alone_yields_its_tier_and_a_partial_one_does_not():
    # Row 6 of #282's table is what makes `exact` load-bearing, and this is the whole reason the
    # definition above is an equality rather than a containment.
    (exact,) = _claims(SUNSCREEN, ("recent",))
    assert launch_verdict(launch_interval("oy:A1", [exact]), REFERENCE).verdict == "new_3m"
    (partial,) = _claims(SUNSCREEN, ("weakened",))
    assert partial.match_strength == PARTIAL
    assert launch_verdict(launch_interval("oy:A1", [partial]), REFERENCE).verdict == "unknown"


def test_a_later_refiling_of_one_line_does_not_make_the_product_new():
    # The review's main finding, from these very fixtures: `base` and `recent` are the same
    # registered name filed five years apart, and 91 names in the real ledger are filed twice (13 of
    # them years apart). Both claims are `exact`, so no strength gate can catch it -- the axis's
    # bound has to be the EARLIEST of its own claims.
    claims = _claims(SUNSCREEN, ("base", "recent"))
    assert {c.match_strength for c in claims} == {EXACT}
    interval = launch_interval("oy:A1", claims)
    assert interval.earliest == REGISTRATIONS["base"].report_date
    assert launch_verdict(interval, REFERENCE).verdict != "new_3m"
    assert launch_verdict(interval, REFERENCE).verdict == "unknown"


def test_a_refill_or_set_filing_resolves_to_the_base_lines_earliest_filing():
    # `normalize_name`'s GLUED rule strips the refill, set and gift words, so a 2026 refill or
    # gift-set registration of a 2021 line folds to that line's key and is `exact` at its own
    # date. Nothing about the join is wrong -- it is the same line -- so only the fold answers it.
    claims = _claims(SUNSCREEN, ("base", "refill", "gift_set"))
    assert len(claims) == 3
    assert {c.match_strength for c in claims} == {EXACT}
    assert launch_interval("oy:A1", claims).earliest == REGISTRATIONS["base"].report_date


def test_a_filing_after_a_held_review_is_a_conflict_rather_than_a_launch():
    # The second measured trap: the MFDS join took a later variant, and the product's own held
    # review predates the filing. The pair crosses, and #282's row 2 answers `conflict`.
    (lower,) = _claims(SUNSCREEN, ("late",))
    upper = review_claim(ANCHOR, date(2023, 1, 9), AT)
    assert launch_verdict(launch_interval("oy:A1", [lower, upper]), REFERENCE).verdict == "conflict"
