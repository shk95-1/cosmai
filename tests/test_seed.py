"""The seed puts the 2026-08-23 slice + eval rows into `needs` and a second run changes nothing."""

from __future__ import annotations

import pytest

from db import seed
from db.seed._common import connect

pytestmark = pytest.mark.postgres

# 기대 행 수. 출처: 각 슬라이스 README.md 산출물 표 + eval/README.md 건수 열.
EXPECTED = {
    # brand 859 surface + 109 alias − 18 중복 surface, ingredient 32행 → 42 surface
    "entity_lexicon": 992,
    # aspects_generic.py GENERIC 20 + polarity.py ASPECTS 15 + SPECIFIC 37 − 선블록 중복 2
    "aspect_lexicon": 70,
    # AXIS_MAP 25축 × 그 축을 실제로 내놓은 사이트. 올영 25 = slice-p1-category-gap/
    # site_topic_raw.csv 의 topic_group, 다이소 9 = 같은 곳 site_answer_raw.csv 의 question_name.
    "site_axis_map": 34,
    # 두 슬라이스 어휘의 합집합 = aspect_lexicon 70행의 distinct aspect (A17)
    "need_key": 38,
    # p1 extract_candidates.py 의 NAME_CAT 14 + CAT_MAP 6 (A18)
    "category_map": 20,
    # eval/panel/channels_v1.csv 43채널의 명부 한 판본 (#31)
    "panel_roster": 1,
    "panel_channel": 43,
    # eval/mfds/mfds_items_v1.csv: the MFDS filing ledger at ydc v0.4.0, one snapshot row (#55).
    # 4,736 is the file's line count -- the header is one of those lines.
    "mfds_snapshot": 1,
    "mfds_registration": 4735,
    # eval/README.md: polarity 400 + wish 220(tune100 + holdout60 + blind60_v2)
    # + brand_link 120 + product_match 80
    "labeled_set": 820,
    "product_ref": 154,  # slice-suncare 18 + slice-p2 145 − 겹침 9
    "product_member": 348,  # slice-suncare 29 + slice-p2 341 − 같은 (source, product_key) 22
    "product_ref_candidate": 230,  # slice-p2-ranking-dynamics/product_ref_candidates.csv (A13)
    "product_denominator": 38,  # slice-p1-category-gap/README.md
    "rank_daily": 17948,  # slice-p2-ranking-dynamics/README.md
    "price_event": 363,  # slice-p2-ranking-dynamics/README.md
    # 005 로 extractor_version 이 자연키에 들어간 뒤 두 슬라이스는 서로 흡수되지 않는다:
    # slice-suncare 2,266 + slice-p1 13,780, 충돌 0 (전에는 548행이 suncare 행에 흡수됐다).
    "need_mention": 16046,
    "wish_mention": 18489,  # slice-p9-wish-mining/README.md
    "brand_mention": 48481,  # slice-p3-youtube-brand-link/README.md
    "analysis_run": 3,  # 슬라이스마다 run 하나: seed:slice-{suncare,p1,p9}
    "metrics_need": 346,  # suncare 15 + 30 (suncare run) + p1 301 (p1 run)
    "metrics_wish": 601,  # slice-p9-wish-mining/wish_aggregates.csv
    # 슬라이스가 아니라 운영 선언이다 -- 크론 14줄과 1:1 이고 그 대조는
    # tests/test_pipeline_stage.py 가 한다 (#138).
    "pipeline_stage": 14,
    # 노드 28(단계 14 + 저장소 14)을 잇는 엣지. 실재와의 대조는
    # tests/test_pipeline_edge.py 가 한다 (#141).
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


# 002 가 더한 컬럼은 행 수로는 보이지 않는다: 값이 실제로 들어갔는지 따로 센다.
FILLED = {
    # aspect_lexicon 70 = p1-v2.2 55 + suncare-v2.2 13 + shared 2 (formats.md §ruleset)
    "select count(*) from aspect_lexicon where ruleset = ''": 0,
    "select count(*) from aspect_lexicon where ruleset in ('suncare-v2.2', 'shared')": 15,
    "select count(*) from aspect_lexicon where ruleset in ('p1-v2.2', 'shared')": 57,
    "select count(*) from aspect_lexicon where scope = 'category' and priority <> 0": 0,
    "select count(*) from aspect_lexicon where scope = 'generic' and priority <> 1": 0,
    # 파일 순서가 테이블에 남아야 name_keyword 정규식의 우선순위가 재현된다 (A18)
    "select count(distinct priority) from category_map": 20,
    "select count(*) from category_map where priority = 0": 0,
    "select count(*) from product_denominator where category is null": 0,
    "select count(*) from product_denominator where aggregate_version <> 'slice-p1'": 0,
    "select count(*) from rank_daily where n_present is null or aggregate_version <> 'slice-p2'": 0,
    "select count(*) from price_event where n_pre is null or n_post24 is null": 0,
    # wish 의 두 holdout 은 split 도 ref 문법도 같다 — extra.set 만이 블라인드 셋을 가른다 (#1 평가 하네스).
    "select count(*) from labeled_set where extra->>'set' = 'blind60_v2'": 60,
    "select count(*) from labeled_set where task = 'wish_class' and extra->>'set' is null": 0,
    # A20: 두 언급 테이블이 같은 댓글에 같은 키를 쓴다.
    "select count(*) from wish_mention where ref not like '%/%'": 0,
    # slice-suncare/metrics.csv 15행만 유튜브 집계를 갖는다 (A1).
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


# F-1: 사전 v1 은 버전으로만 바뀐다(에픽 판정 9). 재적재가 채우는 것은 002 가 뒤늦게 더한 빈 컬럼뿐이다.
SENTINEL = "sentinel-not-from-csv"


def test_the_seed_backfills_only_the_aspect_rows_that_have_no_ruleset(needs_runtime_url: str):
    seed.run_all(needs_runtime_url, only=("lexicon",))
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        # 002 이전의 운영 상태: v1 행은 있고 새 두 컬럼만 DEFAULT. 한 행만 사람이 넣은 값을 갖는다.
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
    # 비어 있던 69행은 CSV 값으로 채워지고(''는 남지 않는다), 센티널 행은 그대로다.
    # 첫 행(min(id))은 generic 이라 p1-v2.2 55행 중 하나이고, generic priority 도 그 한 행만큼 덜 채워진다.
    assert by_ruleset == {"p1-v2.2": 54, "suncare-v2.2": 13, "shared": 2, SENTINEL: 1}
    assert generic_priority == (19,)
    # 다른 사전 적재는 이 조작에 영향받지 않는다.
    assert entities == (992,)
