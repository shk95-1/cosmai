"""The seed puts the 2026-08-23 slice + eval rows into `needs` and a second run changes nothing."""

from __future__ import annotations

import pytest

from db import seed
from db.seed._common import connect

pytestmark = pytest.mark.postgres

# Expected row counts. Source: each slice's README.md output table plus eval/README.md's count column.
EXPECTED = {
    # brand 859 surface + 109 alias - 18 duplicate surface, ingredient 32 rows -> 42 surface
    "entity_lexicon": 992,
    # aspects_generic.py GENERIC 20 + polarity.py ASPECTS 15 + SPECIFIC 37 − 선블록 중복 2
    "aspect_lexicon": 70,
    # AXIS_MAP's 25 axes x the sites that actually publish that axis. oliveyoung's 25 = the topic_group
    # in slice-p1-category-gap/site_topic_raw.csv, daisomall's 9 = the question_name in that same
    # site_answer_raw.csv.
    "site_axis_map": 34,
    # The union of the two slices' vocabulary = the distinct aspect among aspect_lexicon's 70 rows (A17)
    "need_key": 38,
    # p1 extract_candidates.py's NAME_CAT 14 + CAT_MAP 6 (A18)
    "category_map": 20,
    # One roster version of eval/panel/channels_v1.csv's 43 channels (#31)
    "panel_roster": 1,
    "panel_channel": 43,
    # eval/mfds/mfds_items_v1.csv: the MFDS filing ledger at ydc v0.4.0, one snapshot row (#55).
    # 4,736 is the file's line count -- the header is one of those lines.
    "mfds_snapshot": 1,
    "mfds_registration": 4735,
    # eval/README.md: polarity 400 + wish 220 (tune100 + holdout60 + blind60_v2)
    # + brand_link 120 + product_match 80
    "labeled_set": 820,
    "product_ref": 154,  # slice-suncare 18 + slice-p2 145 - 9 overlapping
    "product_member": 348,  # slice-suncare 29 + slice-p2 341 - 22 sharing the same (source, product_key)
    "product_ref_candidate": 230,  # slice-p2-ranking-dynamics/product_ref_candidates.csv (A13)
    "product_denominator": 38,  # slice-p1-category-gap/README.md
    "rank_daily": 17948,  # slice-p2-ranking-dynamics/README.md
    "price_event": 363,  # slice-p2-ranking-dynamics/README.md
    # After 005 put extractor_version into the natural key, the two slices no longer absorb each
    # other's rows: slice-suncare 2,266 + slice-p1 13,780, 0 collisions (before, 548 rows used to be
    # absorbed into the suncare rows).
    "need_mention": 16046,
    "wish_mention": 18489,  # slice-p9-wish-mining/README.md
    "brand_mention": 48481,  # slice-p3-youtube-brand-link/README.md
    "analysis_run": 3,  # one run per slice: seed:slice-{suncare,p1,p9}
    "metrics_need": 346,  # suncare 15 + 30 (suncare run) + p1 301 (p1 run)
    "metrics_wish": 601,  # slice-p9-wish-mining/wish_aggregates.csv
    # An operational declaration, not a slice -- 1:1 with the cron's 14 lines, and
    # tests/test_pipeline_stage.py checks that pairing (#138).
    "pipeline_stage": 14,
    # The edges linking 28 nodes (14 stages + 14 stores). Checked against reality by
    # tests/test_pipeline_edge.py (#141).
    "pipeline_edge": 31,
}


def test_seed_loads_the_slice_row_counts_and_is_idempotent(needs_runtime_url: str):
    # Production loads as needs_runtime, not needs_migrator -- prove the seed runs under that role too.
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT current_user")
        row = cur.fetchone()
    assert row == ("needs_runtime",)
    first = seed.run_all(needs_runtime_url)
    assert first == EXPECTED
    second = seed.run_all(needs_runtime_url)
    assert second == EXPECTED


# A column 002 added does not show up in a row count: this counts separately whether a value actually
# went in.
FILLED = {
    # aspect_lexicon 70 = p1-v2.2 55 + suncare-v2.2 13 + shared 2 (formats.md §ruleset)
    "select count(*) from aspect_lexicon where ruleset = ''": 0,
    "select count(*) from aspect_lexicon where ruleset in ('suncare-v2.2', 'shared')": 15,
    "select count(*) from aspect_lexicon where ruleset in ('p1-v2.2', 'shared')": 57,
    "select count(*) from aspect_lexicon where scope = 'category' and priority <> 0": 0,
    "select count(*) from aspect_lexicon where scope = 'generic' and priority <> 1": 0,
    # The file order has to survive in the table for name_keyword's regex priority to be reproduced (A18)
    "select count(distinct priority) from category_map": 20,
    "select count(*) from category_map where priority = 0": 0,
    "select count(*) from product_denominator where category is null": 0,
    "select count(*) from product_denominator where aggregate_version <> 'slice-p1'": 0,
    "select count(*) from rank_daily where n_present is null or aggregate_version <> 'slice-p2'": 0,
    "select count(*) from price_event where n_pre is null or n_post24 is null": 0,
    # wish's two holdouts share both the split and the ref grammar -- only extra.set tells the blind
    # set apart (#1's evaluation harness).
    "select count(*) from labeled_set where extra->>'set' = 'blind60_v2'": 60,
    "select count(*) from labeled_set where task = 'wish_class' and extra->>'set' is null": 0,
    # A20: the two mention tables use the same key for the same comment.
    "select count(*) from wish_mention where ref not like '%/%'": 0,
    # Only slice-suncare/metrics.csv's 15 rows carry a youtube aggregate (A1).
    "select count(*) from metrics_need where yt_neg is not null": 15,
    "select count(*) from metrics_need where denom_low is not null": 331,
    "select count(*) from metrics_need where aspect_scope is not null": 301,
    "select count(*) from metrics_wish where videos is null or example is null": 0,
    # suncare 2,266(전부 선블록) + p1 의 lexicon_category 있는 11,537, 충돌 0 (B10 · 005)
    "select count(*) from need_mention where lexicon_category is not null": 13803,
    # #92: seeded runs must close out like production runs, not linger with finished_at NULL.
    "select count(*) from analysis_run where finished_at is null": 0,
}


def test_the_seed_fills_the_columns_the_audit_added(needs_runtime_url: str):
    seed.run_all(needs_runtime_url)
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        got = {}
        for query, _ in FILLED.items():
            cur.execute(query)  # type: ignore[arg-type]
            row = cur.fetchone()
            got[query] = int(row[0]) if row else -1
    assert got == FILLED


# F-1: the v1 lexicon only ever changes by version (epic judgment 9). Re-loading only fills the empty
# columns 002 added afterward.
SENTINEL = "sentinel-not-from-csv"


def test_the_seed_backfills_only_the_aspect_rows_that_have_no_ruleset(needs_runtime_url: str):
    seed.run_all(needs_runtime_url, only=("lexicon",))
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        # The production state before 002: v1 rows exist and only the two new columns sit at DEFAULT.
        # Only one row carries a value a person put there.
        cur.execute("UPDATE aspect_lexicon SET ruleset = '', priority = 0")
        cur.execute(
            "UPDATE aspect_lexicon SET ruleset = %s WHERE id = (SELECT min(id) FROM aspect_lexicon)",
            (SENTINEL,),
        )
        conn.commit()
    seed.run_all(needs_runtime_url, only=("lexicon",))
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT ruleset, count(*) FROM aspect_lexicon GROUP BY ruleset")
        by_ruleset = dict(cur.fetchall())
        cur.execute("SELECT count(*) FROM aspect_lexicon WHERE scope = 'generic' AND priority = 1")
        generic_priority = cur.fetchone()
        cur.execute("SELECT count(*) FROM entity_lexicon")
        entities = cur.fetchone()
    # The 69 rows that were empty are filled with the CSV value ('' does not survive), and the sentinel
    # row stands as it was.
    # The first row (min(id)) is generic, so it is one of p1-v2.2's 55 rows, and generic priority ends
    # up one row short of full for the same reason.
    assert by_ruleset == {"p1-v2.2": 54, "suncare-v2.2": 13, "shared": 2, SENTINEL: 1}
    assert generic_priority == (19,)
    # No other lexicon load is affected by this mutation.
    assert entities == (992,)
