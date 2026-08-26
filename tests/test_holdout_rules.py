"""홀드아웃의 규칙 (포크 #51, 계약 `contracts/interfaces.md` §홀드아웃).

ydc `holdout_commerce.py` 의 `demo()` 가 못 박은 입력·출력이 여기 그대로 온다 -- 승격이 답을 바꾸지
않았다는 말은 같은 입력에 같은 답이 나온다는 뜻이고, 우리 원천으로는 그 입력을 재현할 수 없으므로
출처가 자기 파일에 적어 둔 그 입력을 든다.

**두 팔을 가르는 일은 여기 없다** -- 그것은 청크 색인이 지는 일이라 `tests/test_holdout_pipeline.py` 의
자리다. 여기가 지는 것은 갈라 놓은 뒤의 셈: 두 분모 · 순위 게이트 · 표준화 · 바스켓 · 판정 네 갈래다.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from analysis import holdout
from analysis.trend import MIN_MENTIONS

ROOT = Path(__file__).resolve().parents[1]
INTERFACES = ROOT / "contracts" / "interfaces.md"
AT = datetime(2026, 8, 20, tzinfo=UTC)
AXIS = ("백탁", "자극_눈시림", "발림성")


def _review(*topics: str, platform: str = "oliveyoung", product: str = "p1", day: int = 0) -> holdout.Review:
    return holdout.Review(
        platform=platform, product_key=product, captured_at=AT + timedelta(days=day), topics=topics
    )


def _arm(name: str, reviews: list[holdout.Review], axis: tuple[str, ...] = AXIS) -> holdout.Arm:
    return holdout.arm(name, reviews, axis)


# ---------------------------------------------------------------- 단위와 두 분모


def test_a_topic_named_twice_in_one_review_is_counted_once():
    """ydc `holdout_commerce.demo()` 의 두 번째 단언 -- "여러 표현이 한 건에 있어도 한 번만 센다"."""
    counted = _arm(holdout.SEEN, [_review("백탁", "백탁", "백탁")])
    assert counted.documents["백탁"] == 1
    assert counted.mentions == 1, "언급 합이 3 이 되면 구성비의 분모가 부풀려진다"


def test_the_two_denominators_are_not_the_same_number():
    """`rate` 는 그 팔의 리뷰 수로, `share` 는 주제 언급 합으로 나눈다. 섞으면 뜻이 없어진다."""
    counted = _arm(holdout.SEEN, [_review("백탁", "발림성"), _review(), _review()])
    assert counted.reviews == 3 and counted.mentions == 2
    assert holdout.rate(counted.documents["백탁"], counted.reviews) == pytest.approx(100 / 3)
    assert holdout.share(counted.documents["백탁"], counted.mentions) == pytest.approx(50.0)


def test_a_review_that_mentions_nothing_still_sits_in_the_rate_denominator():
    """주제가 하나도 안 걸린 리뷰도 그 팔의 리뷰다. 빼면 언급률이 조용히 커진다."""
    counted = _arm(holdout.SEEN, [_review("백탁"), _review()])
    assert counted.reviews == 2
    assert holdout.rate(counted.documents["백탁"], counted.reviews) == pytest.approx(50.0)


def test_a_topic_off_the_axis_never_enters_either_denominator():
    counted = _arm(holdout.SEEN, [_review("백탁", "추천_재구매")])
    assert counted.mentions == 1
    assert "추천_재구매" not in counted.documents


def test_an_empty_arm_answers_zero_instead_of_dividing():
    assert holdout.rate(0, 0) == 0.0
    assert holdout.share(0, 0) == 0.0


# ---------------------------------------------------------------- 순위 게이트


def _gated(seen_hits: int) -> holdout.TopicRow:
    seen = [_review("백탁") for _ in range(seen_hits)] + [_review("발림성") for _ in range(9)]
    hold = [_review("백탁"), *(_review("발림성") for _ in range(9))]
    rows = {row.topic_key: row for row in holdout.topics(_arm("a", seen), _arm("b", hold), AXIS)}
    return rows["백탁"]


def test_a_topic_under_the_sample_gate_has_no_rank_at_all():
    """13주제 축의 꼬리는 0으로 묶여 있다. 그 자리에서 매긴 등수는 동률 정렬의 산물이지 순위가 아니다."""
    assert _gated(MIN_MENTIONS - 1).seen_rank is None
    assert _gated(MIN_MENTIONS - 1).holdout_rank is None


def test_the_gate_opens_exactly_at_the_sample_floor():
    """문턱이 실제로 `MIN_MENTIONS` 인가 -- 하나 옆에서 답이 갈려야 이 게이트가 검사된 것이다."""
    assert _gated(MIN_MENTIONS).seen_rank is not None


def test_ranks_run_over_the_gated_topics_only():
    """게이트를 못 넘은 주제가 순위 자리를 먹으면 1위가 2위가 된다."""
    seen = [
        *(_review("백탁") for _ in range(9)),
        *(_review("발림성") for _ in range(5)),
        _review("자극_눈시림"),
    ]
    rows = {row.topic_key: row for row in holdout.topics(_arm("a", seen), _arm("b", seen), AXIS)}
    assert rows["백탁"].seen_rank == 1 and rows["발림성"].seen_rank == 2
    assert rows["자극_눈시림"].seen_rank is None


def test_the_gate_is_the_seen_arm_because_that_is_the_baseline():
    """게이트를 홀드아웃에 걸면 새 표본이 얇다는 이유로 기존 순위가 사라진다 -- 물음이 거꾸로 선다."""
    seen = [_review("백탁") for _ in range(9)]
    hold = [_review("백탁"), _review()]
    (row,) = [r for r in holdout.topics(_arm("a", seen), _arm("b", hold), AXIS) if r.topic_key == "백탁"]
    assert row.seen_rank == 1 and row.holdout_rank == 1


# ---------------------------------------------------------------- 두 축이 갈리는 자리


def test_a_level_that_rises_across_every_topic_leaves_the_shares_alone():
    """모든 주제의 언급률이 함께 오르면 구성비는 그대로다 -- **수집이 바뀐 것**이지 그 말이 는 것이 아니다.
    구성비만 보면 이 사건이 안 보이고, 언급률만 보면 자리 이동과 구분되지 않는다."""
    seen = [_review("백탁"), _review("발림성"), _review(), _review()]
    hold = [_review("백탁"), _review("발림성")]
    rows = {row.topic_key: row for row in holdout.topics(_arm("a", seen), _arm("b", hold), AXIS)}
    assert rows["백탁"].seen_share == pytest.approx(rows["백탁"].holdout_share)
    assert rows["백탁"].holdout_rate - rows["백탁"].seen_rate == pytest.approx(25.0)


def test_one_topic_rising_alone_moves_both_axes():
    seen = [_review("백탁"), _review("발림성")]
    hold = [_review("백탁"), _review("백탁"), _review("발림성")]
    rows = {row.topic_key: row for row in holdout.topics(_arm("a", seen), _arm("b", hold), AXIS)}
    assert rows["백탁"].holdout_share > rows["백탁"].seen_share
    assert rows["백탁"].holdout_rate > rows["백탁"].seen_rate


# ---------------------------------------------------------------- 플랫폼 구성과 표준화


def test_the_platform_mix_is_read_off_both_arms():
    seen = [_review(platform="oliveyoung"), _review(platform="daisomall")]
    hold = [_review(platform="oliveyoung")]
    rows = {row.platform: row for row in holdout.platforms(_arm("a", seen), _arm("b", hold))}
    assert rows["oliveyoung"].seen_mix == pytest.approx(50.0)
    assert rows["daisomall"].holdout_reviews == 0 and rows["daisomall"].holdout_mix == 0.0


def test_standardizing_takes_the_composition_effect_out():
    """플랫폼마다의 언급률은 그대로인데 구성만 바뀐 경우. **원값은 움직이고 표준화 값은 안 움직인다** --
    그 둘이 같이 움직이면 이 표는 아무것도 갈라내지 못한다 (계약 §홀드아웃)."""
    seen = [
        *(_review("백탁", platform="oliveyoung") for _ in range(5)),
        *(_review(platform="oliveyoung") for _ in range(5)),
        *(_review(platform="daisomall") for _ in range(10)),
    ]
    # 같은 플랫폼별 언급률(oliveyoung 50% · daisomall 0%)인데 구성만 8:2 로 바뀌었다.
    hold = [
        *(_review("백탁", platform="oliveyoung") for _ in range(4)),
        *(_review(platform="oliveyoung") for _ in range(4)),
        *(_review(platform="daisomall") for _ in range(2)),
    ]
    (row,) = [r for r in holdout.standardize(seen, hold, AXIS) if r.topic_key == "백탁"]
    assert row.seen_rate == pytest.approx(25.0)
    assert row.holdout_rate == pytest.approx(40.0), "구성이 바뀌면 원값은 움직인다"
    assert row.standardized_rate == pytest.approx(25.0), "구성 효과를 빼면 남는 것이 없다"


def test_a_platform_missing_from_the_holdout_enters_the_weighted_sum_as_zero():
    """ydc 와 같은 자리다(`rate(b_texts.get(p, []), t)` 가 0.0). 조용한 0 이 아니라 표가 말하는 0 이다."""
    seen = [
        *(_review("백탁", platform="oliveyoung") for _ in range(5)),
        *(_review(platform="daisomall") for _ in range(5)),
    ]
    hold = [_review("백탁", platform="oliveyoung")]
    (row,) = [r for r in holdout.standardize(seen, hold, AXIS) if r.topic_key == "백탁"]
    assert row.holdout_rate == pytest.approx(100.0)
    assert row.standardized_rate == pytest.approx(50.0), "daisomall 칸이 0 으로 들어간다"
    rows = {r.platform: r for r in holdout.platforms(_arm("a", seen), _arm("b", hold))}
    assert rows["daisomall"].holdout_reviews == 0


# ---------------------------------------------------------------- 제품 바스켓


def test_the_basket_names_what_the_two_arms_do_not_share():
    seen = [_review(product="a"), _review(product="b")]
    hold = [_review(product="b"), _review(product="c")]
    made, _rows = holdout.basket(seen, hold, AXIS)
    assert (made.seen_products, made.holdout_products, made.shared) == (2, 2, 1)
    assert (made.seen_only, made.holdout_only) == (1, 1)
    assert (made.seen_reviews, made.holdout_reviews) == (1, 1)


def test_recounting_on_the_shared_products_removes_the_basket_effect():
    """**ydc 에서 실제 원인이었던 자리다** -- 수집기가 그 주에 긁은 제품이 달랐다. 홀드아웃에만 있는
    제품이 그 말을 몰고 오면 전체 언급률은 오르지만 같은 제품만으로는 그대로다."""
    seen = [_review("백탁", product="a"), _review(product="a")]
    hold = [
        _review("백탁", product="a"),
        _review(product="a"),
        *(_review("백탁", product="new") for _ in range(6)),
    ]
    made, rows = holdout.basket(seen, hold, AXIS)
    row = {r.topic_key: r for r in rows}["백탁"]
    assert made.shared == 1
    assert row.seen_rate_all == pytest.approx(50.0)
    assert row.holdout_rate_shared == pytest.approx(50.0), "같은 제품만으로는 재현된다"
    assert holdout.rate(2 + 6 - 1, 8) > row.holdout_rate_shared, "전체로는 오른다"


def test_a_basket_with_no_shared_product_says_so_instead_of_answering_zero():
    seen = [_review("백탁", product="a")]
    hold = [_review("백탁", product="b")]
    made, rows = holdout.basket(seen, hold, AXIS)
    assert made.shared == 0
    assert rows == (), "교집합이 없으면 바스켓 표는 서지 않는다 -- 0% 는 답이 아니라 없음이다"


# ---------------------------------------------------------------- 창


def test_a_holdout_that_starts_after_the_baseline_ended_is_a_new_period():
    seen = _arm("a", [_review(day=0), _review(day=1)])
    hold = _arm("b", [_review(day=2), _review(day=3)])
    assert holdout.window_reading(seen, hold) == holdout.WINDOW_NEW


def test_a_holdout_that_overlaps_the_baseline_is_the_same_window_grown_longer():
    """ydc 가 "새 기간이 아니라 같은 창이 하루 반 길어진 것뿐"이라고 적은 그 물음이다."""
    seen = _arm("a", [_review(day=0), _review(day=3)])
    hold = _arm("b", [_review(day=1), _review(day=5)])
    assert holdout.window_reading(seen, hold) == holdout.WINDOW_EXTENDED


# ---------------------------------------------------------------- 판정 네 갈래


def _verdict(seen: list[holdout.Review], hold: list[holdout.Review]) -> str:
    return holdout.verdict(holdout.topics(_arm("a", seen), _arm("b", hold), AXIS))


def test_a_level_inside_the_threshold_is_reproduced():
    seen = [*(_review("백탁") for _ in range(100)), *(_review() for _ in range(100))]
    hold = [*(_review("백탁") for _ in range(101)), *(_review() for _ in range(99))]
    assert _verdict(seen, hold) == holdout.VERDICT_REPRODUCED


def test_a_level_past_the_threshold_with_the_top_intact_keeps_the_conclusion():
    """**우리 결론은 순위를 쓰므로 유지된다** -- 수준이 왜 움직였는지는 창·구성·바스켓이 답한다."""
    seen = [
        *(_review("백탁") for _ in range(10)),
        *(_review("발림성") for _ in range(5)),
        *(_review() for _ in range(85)),
    ]
    hold = [
        *(_review("백탁") for _ in range(40)),
        *(_review("발림성") for _ in range(5)),
        *(_review() for _ in range(55)),
    ]
    assert _verdict(seen, hold) == holdout.VERDICT_RANK_ONLY


def test_a_swapped_top_says_the_conclusion_has_to_be_looked_at_again():
    seen = [
        *(_review("백탁") for _ in range(40)),
        *(_review("발림성") for _ in range(10)),
        *(_review() for _ in range(50)),
    ]
    hold = [
        *(_review("백탁") for _ in range(10)),
        *(_review("발림성") for _ in range(40)),
        *(_review() for _ in range(50)),
    ]
    assert _verdict(seen, hold) == holdout.VERDICT_BROKEN


def test_no_topic_clearing_the_gate_is_not_called_reproduction():
    """공회전을 `재현` 이라고 부르면 없는 근거로 재현을 주장하게 된다 (계약 §홀드아웃)."""
    seen = [_review("백탁"), _review()]
    hold = [_review("백탁"), _review()]
    assert _verdict(seen, hold) == holdout.VERDICT_THIN


def test_the_material_threshold_is_the_edge_the_contract_says_it_is():
    """`MATERIAL_PP` 를 넓히면 이 두 줄이 같은 답을 하게 된다 -- 문턱이 문턱인지 여기서만 검사된다."""
    seen = [*(_review("백탁") for _ in range(100)), *(_review() for _ in range(900))]
    edge = [*(_review("백탁") for _ in range(115)), *(_review() for _ in range(885))]
    over = [*(_review("백탁") for _ in range(116)), *(_review() for _ in range(884))]
    assert _verdict(seen, edge) == holdout.VERDICT_REPRODUCED, "차가 정확히 1.5%p 면 재현이다"
    assert _verdict(seen, over) != holdout.VERDICT_REPRODUCED, "1.6%p 는 사람이 본다"


def test_the_top_the_rule_compares_is_as_deep_as_rank_top():
    """1위는 그대로인데 2·3위가 뒤집힌 경우. `RANK_TOP` 을 1 로 좁히면 이것이 `순위 재현` 이 되어
    결론이 유지된다고 말하게 된다 -- 깊이가 실제로 2 인지 여기서만 검사된다."""
    seen = [
        *(_review("백탁") for _ in range(50)),
        *(_review("자극_눈시림") for _ in range(30)),
        *(_review("발림성") for _ in range(10)),
        *(_review() for _ in range(10)),
    ]
    hold = [
        *(_review("백탁") for _ in range(50)),
        *(_review("자극_눈시림") for _ in range(10)),
        *(_review("발림성") for _ in range(30)),
        *(_review() for _ in range(10)),
    ]
    rows = holdout.topics(_arm("a", seen), _arm("b", hold), AXIS)
    assert [row.seen_rank for row in rows if row.topic_key == "백탁"] == [1], "1위는 그대로다"
    assert holdout.verdict(rows) == holdout.VERDICT_BROKEN


def test_the_level_threshold_wins_before_the_rank_is_asked():
    """ydc `report` 의 갈래 순서다: `worst <= 1.5` 가 먼저다. 1.5%p 안에서 순서가 바뀌는 것은 표본
    흔들림이라는 것이 그 문턱의 뜻이라, 거기서 순위를 되물으면 문턱이 두 번 서게 된다."""
    seen = [
        *(_review("백탁") for _ in range(21)),
        *(_review("발림성") for _ in range(20)),
        *(_review() for _ in range(59)),
    ]
    hold = [
        *(_review("백탁") for _ in range(20)),
        *(_review("발림성") for _ in range(21)),
        *(_review() for _ in range(59)),
    ]
    rows = holdout.topics(_arm("a", seen), _arm("b", hold), AXIS)
    ranked = {row.topic_key: (row.seen_rank, row.holdout_rank) for row in rows if row.seen_rank}
    assert ranked["백탁"] == (1, 2) and ranked["발림성"] == (2, 1), "상위 둘이 실제로 뒤집혔다"
    assert holdout.verdict(rows) == holdout.VERDICT_REPRODUCED


# ---------------------------------------------------------------- 계약 표와 대조


def _constant_rows() -> list[list[str]]:
    """§홀드아웃 상수 의 표만. 같은 머리글이 §대조 에도 있으므로 절 제목에서 내려와 찾는다."""
    lines = INTERFACES.read_text(encoding="utf-8").splitlines()
    start = lines.index("### 홀드아웃 상수 (`analysis/holdout` 한 곳에 모여 있다)")
    rows: list[list[str]] = []
    for line in lines[start + 3 :]:
        if not line.startswith("|"):
            break
        rows.append([cell.strip() for cell in line.strip("|").split("|")])
    return rows


def test_the_contract_table_and_the_code_say_the_same_numbers():
    pinned = {
        re.findall(r"`([^`]+)`", row[0])[0]: re.findall(r"`([^`]+)`", row[1]) for row in _constant_rows()
    }
    assert float(pinned["MATERIAL_PP"][0]) == holdout.MATERIAL_PP
    assert int(pinned["RANK_TOP"][0]) == holdout.RANK_TOP
    assert int(pinned["MIN_MENTIONS"][-1]) == MIN_MENTIONS


def test_every_pinned_constant_carries_its_reason_and_its_verdict():
    """값 열만 맞추는 것으로는 부족하다 -- 근거가 빠진 줄은 다음 사람에게 다시 민담이 된다."""
    rows = _constant_rows()
    assert len(rows) == 3
    for row in rows:
        assert row[2] and row[3], row
