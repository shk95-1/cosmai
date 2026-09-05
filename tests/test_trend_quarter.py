"""Are the five formulas of the quarterly time series exactly the contract's sentences (fork #5,
`contracts/interfaces.md` §Formulas).

DB 없이 돈다. 수식이 셈에서 갈라져 있는 것이 이 파일이 존재할 수 있는 이유이고, ydc `trend.py` 와의
1:1 골든(`tests/test_trend_golden.py`)이 서는 자리도 같은 갈라짐이다.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

from analysis.trend import DIGITS, METRIC_VERSION, MIN_MENTIONS, WINDOW_QUARTERS, Counts, VideoPanel, rows

ROOT = Path(__file__).resolve().parents[1]
INTERFACES = ROOT / "contracts" / "interfaces.md"
VERSIONING = ROOT / "contracts" / "versioning.md"
ENTRYPOINTS = ROOT / "contracts" / "entrypoints.md"
DDL = ROOT / "contracts" / "ddl" / "needs" / "022_panel_and_quarter.sql"

KEY = {
    "run_id": 1,
    "scope": "선블록",
    "content_type": "long_form",
    "panel_version": 1,
    "panel_role": "product",
}
TOPICS = ("백탁", "발림성")


def _rows(counts: Counts, panel: VideoPanel, source: str = "youtube_video", topics=TOPICS):
    return {
        (row.topic_key, row.quarter): row for row in rows(list(topics), counts, panel, source=source, **KEY)
    }


def _panel(quarters: dict[str, int], per_channel: dict | None = None) -> VideoPanel:
    return VideoPanel(quarters, per_channel or {})


# 한 주제가 세 분기에 걸쳐 있고 가운데 분기는 언급이 0이다 -- 0 셀이 행이 되는지가 첫 불변식이다.
COUNTS = Counts(
    documents={"2024Q2": 20, "2025Q1": 18, "2025Q2": 22},
    mentions={
        ("백탁", "2024Q2"): 6,
        ("발림성", "2024Q2"): 4,
        ("발림성", "2025Q1"): 5,
        ("백탁", "2025Q2"): 9,
        ("발림성", "2025Q2"): 3,
    },  # fmt: skip
    raw={
        ("백탁", "2024Q2"): 6,
        ("발림성", "2024Q2"): 4,
        ("발림성", "2025Q1"): 10,
        ("백탁", "2025Q2"): 9,
        ("발림성", "2025Q2"): 3,
    },  # fmt: skip
    channels={
        ("백탁", "2024Q2"): 3,
        ("발림성", "2024Q2"): 2,
        ("발림성", "2025Q1"): 4,
        ("백탁", "2025Q2"): 5,
        ("발림성", "2025Q2"): 2,
    },  # fmt: skip
)
PANEL = _panel(
    {"2024Q2": 8, "2025Q1": 6, "2025Q2": 10},
    {("백탁", "2024Q2"): {"c1": 4, "c2": 1, "c3": 1}, ("백탁", "2025Q2"): {"c1": 3, "c2": 3}},
)


def test_the_grid_is_dense_so_a_quarter_with_no_mention_is_still_a_row():
    """Delete the 0 cells and persistence's baseline rises, moving every topic's value (§Formulas)."""
    built = _rows(COUNTS, PANEL)
    assert set(built) == {(t, q) for t in TOPICS for q in ("2024Q2", "2025Q1", "2025Q2")}
    empty = built[("백탁", "2025Q1")]
    assert (empty.mentions, empty.composition, empty.unique_ratio, empty.sample_ok) == (0, 0.0, 1.0, False)


def test_the_denominator_closes_on_every_quarter():
    """저장된 표에 `SUM(mentions) GROUP BY quarter` 를 돌리는 사람이 맞으려면 이 등식이 서야 한다."""
    built = _rows(COUNTS, PANEL)
    for quarter in ("2024Q2", "2025Q1", "2025Q2"):
        rows_here = [row for (_, q), row in built.items() if q == quarter]
        assert sum(row.mentions for row in rows_here) == rows_here[0].quarter_mentions
        assert len({row.quarter_mentions for row in rows_here}) == 1


def test_composition_is_a_share_between_topics_not_between_documents():
    built = _rows(COUNTS, PANEL)
    assert built[("백탁", "2024Q2")].composition == round(6 / 10, DIGITS["composition"])
    assert built[("발림성", "2025Q1")].composition == 1.0  # 그 분기의 유일한 언급이다


def test_composition_is_zero_not_null_when_the_quarter_has_no_mention_at_all():
    counts = Counts(documents={"2025Q1": 4}, mentions={}, raw={}, channels={})
    built = _rows(counts, _panel({"2025Q1": 2}))
    assert built[("백탁", "2025Q1")].composition == 0.0
    assert built[("백탁", "2025Q1")].quarter_mentions == 0


def test_velocity_compares_the_same_quarter_of_the_year_before():
    built = _rows(COUNTS, PANEL)
    expected = math.log(9 / 12) - math.log(6 / 10)
    assert built[("백탁", "2025Q2")].velocity_yoy == round(expected, DIGITS["velocity_yoy"])


def test_velocity_is_null_when_the_year_before_is_not_a_quarter_of_this_run():
    assert _rows(COUNTS, PANEL)[("백탁", "2024Q2")].velocity_yoy is None


@pytest.mark.parametrize(("here", "there"), [(4, 9), (9, 4)])
def test_velocity_is_null_unless_both_quarters_clear_the_sample_gate(here: int, there: int):
    """표본 부족을 급등으로 읽지 않는다 -- 한쪽만 넘으면 비율의 변화가 아니라 잡음이다."""
    counts = Counts(
        documents={"2024Q2": 9, "2025Q2": 9},
        mentions={
            ("백탁", "2024Q2"): there,
            ("발림성", "2024Q2"): 9,
            ("백탁", "2025Q2"): here,
            ("발림성", "2025Q2"): 9,
        },  # fmt: skip
        raw={},
        channels={},
    )
    assert _rows(counts, _panel({"2024Q2": 2, "2025Q2": 2}))[("백탁", "2025Q2")].velocity_yoy is None


def test_the_sample_gate_and_the_velocity_gate_are_the_same_number():
    built = _rows(COUNTS, PANEL)
    assert built[("발림성", "2025Q1")].sample_ok is (5 >= MIN_MENTIONS)
    assert built[("발림성", "2025Q2")].sample_ok is False


def test_persistence_counts_the_window_that_ends_at_this_row_not_the_latest_four():
    """창이 짧은 초기 분기에서는 비율만으로 개수를 복원할 수 없어 둘 다 남긴다."""
    quarters = [f"2024Q{i}" for i in (1, 2, 3, 4)] + ["2025Q1"]
    counts = Counts(
        documents=dict.fromkeys(quarters, 10),
        mentions={
            **{("백탁", q): n for q, n in zip(quarters, (0, 1, 2, 3, 4), strict=True)},
            **{("발림성", q): n for q, n in zip(quarters, (10, 9, 8, 7, 6), strict=True)},
        },
        raw={},
        channels={},
    )
    built = _rows(counts, _panel(dict.fromkeys(quarters, 2)))
    assert built[("백탁", "2024Q1")].window_quarters == 1
    assert built[("백탁", "2025Q1")].window_quarters == WINDOW_QUARTERS
    # 기준선은 전 기간 중앙값(0.2)이라 그 위인 분기는 2024Q4·2025Q1 둘이다.
    assert built[("백탁", "2025Q1")].persist_quarters == 2
    assert built[("백탁", "2025Q1")].persistence == round(2 / 4, DIGITS["persistence"])


def test_the_baseline_includes_the_zero_quarters():
    """0 분기를 빼면 중앙값이 올라가 같은 분기의 persistence 가 달라진다."""
    quarters = ["2024Q1", "2024Q2", "2024Q3", "2024Q4"]
    with_zero = Counts(
        documents=dict.fromkeys(quarters, 10),
        mentions={
            ("백탁", "2024Q3"): 4,
            ("백탁", "2024Q4"): 6,
            **{("발림성", q): n for q, n in zip(quarters, (10, 10, 6, 4), strict=True)},
        },  # fmt: skip
        raw={},
        channels={},
    )
    built = _rows(with_zero, _panel(dict.fromkeys(quarters, 2)))
    # 0 분기가 든 기준선은 0.2 라 두 분기가 그 위다. 0 분기를 빼면 기준선이 0.5 로 올라가 하나만 남는다.
    assert [built[("백탁", q)].persist_quarters for q in quarters] == [0, 0, 1, 2]


def test_unique_ratio_is_the_share_of_the_mentions_that_are_not_copy_paste():
    built = _rows(COUNTS, PANEL, source="youtube_comment")
    assert built[("발림성", "2025Q1")].unique_ratio == round(5 / 10, DIGITS["unique_ratio"])
    assert built[("백탁", "2024Q2")].unique_ratio == 1.0


def test_channel_diffusion_is_the_same_number_on_the_comment_row_as_on_the_video_row():
    """Both terms use the channel distribution taken from the videos, so this column does not depend on source
    (§Formulas)."""
    videos = _rows(COUNTS, PANEL)
    comments = _rows(COUNTS, PANEL, source="youtube_comment")
    assert all(videos[key].channel_diffusion == comments[key].channel_diffusion for key in videos)


def test_channel_diffusion_mixes_breadth_and_evenness():
    built = _rows(COUNTS, PANEL)
    breadth = 3 / 8
    evenness = -sum(p * math.log(p) for p in (4 / 6, 1 / 6, 1 / 6)) / math.log(3)
    assert built[("백탁", "2024Q2")].channel_diffusion == round(
        0.5 * breadth + 0.5 * evenness, DIGITS["channel_diffusion"]
    )
    # 한 채널이 독점하면 고름 항은 0 이고, 그 분기에 패널 영상이 없으면 넓이 항도 0 이다.
    alone = _rows(COUNTS, _panel({"2024Q2": 4, "2025Q1": 1, "2025Q2": 1}, {("백탁", "2024Q2"): {"c1": 9}}))
    assert alone[("백탁", "2024Q2")].channel_diffusion == round(0.5 * (1 / 4), DIGITS["channel_diffusion"])


def test_channel_count_is_not_the_numerator_of_the_diffusion_term():
    """Use it as the first term's numerator because the names look alike and the comment rows' diffusion
    changes (§Formulas)."""
    comments = _rows(COUNTS, PANEL, source="youtube_comment")
    assert comments[("백탁", "2024Q2")].channel_count == 3  # 그 source 에서 그 주제를 낸 채널 수
    assert len(PANEL.per_channel[("백탁", "2024Q2")]) == 3
    lonely = _rows(COUNTS, _panel({"2024Q2": 8, "2025Q1": 6, "2025Q2": 10}, {("백탁", "2024Q2"): {"c1": 6}}))
    assert lonely[("백탁", "2024Q2")].channel_count == 3
    assert lonely[("백탁", "2024Q2")].channel_diffusion == round(0.5 * (1 / 8), DIGITS["channel_diffusion"])


# ---------- 계약과 상수가 같은 수를 든다 ----------
def test_the_stored_digits_are_the_digits_the_contract_pins():
    pinned = next(
        line
        for line in INTERFACES.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("Decimal places:")
    )
    assert dict(DIGITS) == {name: int(digits) for name, digits in re.findall(r"`(\w+)` (\d+)", pinned)}


def test_the_sample_gate_is_the_number_the_ddl_checks():
    found = re.search(r"CHECK \(sample_ok = \(mentions >= (\d+)\)\)", DDL.read_text(encoding="utf-8"))
    assert found and int(found.group(1)) == MIN_MENTIONS


def test_versioning_names_the_key_that_carries_the_metric_version():
    """ydc 는 모든 행에 METRIC_VERSION 을 다는데 `analysis_run.versions` 에 그것을 부를 자리가 없었다 (#3)."""
    body = VERSIONING.read_text(encoding="utf-8")
    assert "`metric`" in body, "versioning.md 가 metric 키를 부르지 않는다"
    assert f"`{METRIC_VERSION}`" in body, "versioning.md 가 그 키가 드는 값을 말하지 않는다"


def test_entrypoints_declares_the_subcommand_that_writes_the_quarter_table():
    assert "cosmai trend quarter" in ENTRYPOINTS.read_text(encoding="utf-8")


def test_the_rules_are_copied_from_the_slice_not_imported_from_it():
    """The slice is a read-only reference -- importing it means this unit dies the day #9 deletes the
    directory."""
    body = (ROOT / "analysis" / "trend" / "__init__.py").read_text(encoding="utf-8")
    assert "analysis.slices" not in body and "from topics import" not in body
    assert "ydc `trend.py`" in body and "02440ab" in body, (
        "the source file and the promotion sha are not in the header"
    )
