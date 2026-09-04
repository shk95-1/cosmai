"""판정 상수는 민담이 아니라 계약이다 — `contracts/interfaces.md` §판정 의 표와 `analysis/judge` 대조 (#40).

#3 등급 A 리뷰가 넘긴 문장: `TAU` · `DIFFUSION_TAU` · `EVIDENCE_FLOOR` · `W_EVIDENCE` · `W_SCORE` 는
저장된 값 위에서 맞춰진 산물이라 물려받으면 나중에 아무도 왜 그 값인지 모른다. 그래서 계약 표가 값
옆에 **무엇 위에서 나왔나**와 **채택/재적합 판단**을 함께 들고, 이 파일이 그 표와 코드가 같은 수를
말하는지 본다. 값 열만 맞추는 것으로는 부족해서 3·4열이 비어 있지 않은 것도 검사한다 — 근거가 빠진
줄은 다음 사람에게 다시 민담이 된다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from analysis.judge import (
    DIFFUSION_TAU,
    DIGITS,
    EVIDENCE_FLOOR,
    HOLD_REASONS,
    JUDGEMENT_VERSION,
    MIN_DOCUMENTS,
    NEW_TOPIC_MAX_SHARE,
    NOT_A_VERDICT,
    TAU,
    TREND_TYPES,
    W_EVIDENCE,
    W_SCORE,
)
from analysis.trend import MIN_MENTIONS

ROOT = Path(__file__).resolve().parents[1]
INTERFACES = ROOT / "contracts" / "interfaces.md"
VERSIONING = ROOT / "contracts" / "versioning.md"
ENTRYPOINTS = ROOT / "contracts" / "entrypoints.md"
DDL = ROOT / "contracts" / "ddl" / "needs" / "024_topic_quarter_judgement.sql"
QUARTER_DDL = ROOT / "contracts" / "ddl" / "needs" / "022_panel_and_quarter.sql"
CONSTANTS_HEADER = "| 상수 | 값 | 무엇 위에서 나왔나 (재현 결과) | 판단 |"


def _constant_rows() -> list[list[str]]:
    lines = INTERFACES.read_text(encoding="utf-8").splitlines()
    rows: list[list[str]] = []
    for line in lines[lines.index(CONSTANTS_HEADER) + 2 :]:
        if not line.startswith("|"):
            break
        rows.append([cell.strip() for cell in line.strip("|").split("|")])
    return rows


def _pinned() -> dict[str, list[str]]:
    """상수 이름 → 값 열의 백틱 조각들. 가중치는 `이름 값` 쌍이 여럿이다."""
    return {re.findall(r"`([^`]+)`", row[0])[0]: re.findall(r"`([^`]+)`", row[1]) for row in _constant_rows()}


def _weights(pinned: list[str]) -> dict[str, float]:
    return {name: float(value) for name, value in (piece.split() for piece in pinned)}


def test_the_contract_pins_every_constant_the_module_holds():
    assert set(_pinned()) == {
        "TAU", "DIFFUSION_TAU", "EVIDENCE_FLOOR", "MIN_DOCUMENTS", "NEW_TOPIC_MAX_SHARE",
        "W_EVIDENCE", "W_SCORE",
    }  # fmt: skip


@pytest.mark.parametrize(
    ("name", "value"),
    [("TAU", TAU), ("DIFFUSION_TAU", DIFFUSION_TAU), ("EVIDENCE_FLOOR", EVIDENCE_FLOOR),
     ("MIN_DOCUMENTS", MIN_DOCUMENTS), ("NEW_TOPIC_MAX_SHARE", NEW_TOPIC_MAX_SHARE)],
)  # fmt: skip
def test_a_scalar_constant_is_the_number_the_contract_pins(name: str, value: float):
    assert float(_pinned()[name][0]) == value


def test_the_two_weight_maps_are_the_weights_the_contract_pins():
    assert dict(W_EVIDENCE) == _weights(_pinned()["W_EVIDENCE"])
    assert dict(W_SCORE) == _weights(_pinned()["W_SCORE"])


def test_every_constant_row_says_where_the_value_came_from_and_whether_we_adopted_it():
    """값만 옮기고 근거를 비워 두면 이 표는 민담을 표 모양으로 적은 것에 지나지 않는다."""
    thin = [row[0] for row in _constant_rows() if len(row[2]) < 40 or len(row[3]) < 20]
    assert not thin, thin


def test_the_evidence_weights_are_the_v1_four_renormalised_to_three():
    """`entity_link` 가 없어 계산할 수 없는 넷째(20)를 빼고 남은 셋을 0.8 로 나눈 값이다 — 0으로 깔면
    모든 주제가 조용히 20점 깎여 `EVIDENCE_FLOOR` 가 오작동한다."""
    v1 = {"documents": 35.0, "channels": 25.0, "unique": 20.0}
    assert dict(W_EVIDENCE) == {name: pytest.approx(weight / 0.8) for name, weight in v1.items()}
    assert sum(W_EVIDENCE.values()) == pytest.approx(100.0)


def test_the_score_weights_sum_to_one():
    assert sum(W_SCORE.values()) == pytest.approx(1.0)


def test_the_document_gate_is_the_number_the_quarter_table_already_checks():
    """따로 정의하면 022 의 CHECK 과 조용히 갈린다 — 같은 수여야 하는 것이 아니라 같은 상수여야 한다."""
    found = re.search(r"CHECK \(sample_ok = \(mentions >= (\d+)\)\)", QUARTER_DDL.read_text(encoding="utf-8"))
    assert found and int(found.group(1)) == MIN_DOCUMENTS == MIN_MENTIONS


def test_the_stored_digits_are_the_digits_the_contract_pins():
    pinned = next(
        line for line in INTERFACES.read_text(encoding="utf-8").splitlines() if "판정 자리수:" in line
    )
    assert dict(DIGITS) == {name: int(digits) for name, digits in re.findall(r"`(\w+)` (\d+)", pinned)}


def test_the_ddl_stores_those_digits_rather_than_a_bare_numeric():
    """맨 numeric 이면 저장이 자리수를 지키지 않아 같은 run 이 두 벌의 값을 갖는다 (022 리뷰 M1)."""
    body = DDL.read_text(encoding="utf-8")
    for name, digits in DIGITS.items():
        found = re.search(rf"^\s*{name}\s+numeric\((\d+),(\d+)\)", body, re.MULTILINE)
        assert found, f"{name} 은 024 에 numeric(p,s) 로 서 있지 않다"
        assert int(found.group(2)) == digits


def test_the_ddl_closes_the_same_type_vocabulary_the_module_holds():
    found = re.search(
        r"trend_type\s+text NOT NULL CHECK \(trend_type IN \((.*?)\)\)",
        DDL.read_text(encoding="utf-8"),
        re.DOTALL,
    )
    assert found
    assert set(re.findall(r"'([^']+)'", found.group(1))) == set(TREND_TYPES) | set(NOT_A_VERDICT)


def test_the_ddl_closes_the_same_hold_reason_vocabulary():
    found = re.search(
        r"hold_reason\s+text NOT NULL DEFAULT '' CHECK \(hold_reason IN \((.*?)\)\)",
        DDL.read_text(encoding="utf-8"),
        re.DOTALL,
    )
    assert found
    assert set(re.findall(r"'([^']*)'", found.group(1))) == {"", *HOLD_REASONS}


def test_versioning_names_the_key_that_carries_the_judgement_version():
    """상수가 바뀌면 정의가 바뀐 것이고, A19 아래에서 그것을 부를 자리는 run 하나뿐이다."""
    body = VERSIONING.read_text(encoding="utf-8")
    assert "`judgement`" in body, "versioning.md 가 judgement 키를 부르지 않는다"
    assert f"`{JUDGEMENT_VERSION}`" in body, "versioning.md 가 그 키가 드는 값을 말하지 않는다"


def test_entrypoints_declares_the_subcommand_that_writes_the_judgement_table():
    assert "cosmai trend judge" in ENTRYPOINTS.read_text(encoding="utf-8")


def test_the_rules_are_copied_from_the_slice_not_imported_from_it():
    """The slice is a read-only reference -- importing it means this unit dies the day #9 deletes the
    directory."""
    body = (ROOT / "analysis" / "judge" / "__init__.py").read_text(encoding="utf-8")
    assert "analysis.slices" not in body
    assert "ydc `judge.py`" in body and "02440ab" in body, (
        "the source file and the promotion sha are not in the header"
    )
