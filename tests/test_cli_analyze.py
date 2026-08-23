"""`cosmai analyze <stage>`: 이 이슈에서는 골격뿐이고 종료 코드 규약만 지킨다 (entrypoints.md)."""

from __future__ import annotations

import pytest

from cosmai.cli import STAGES, main


def test_the_stage_list_is_the_one_the_contract_names():
    assert STAGES == ("link", "polarity", "aggregate", "all")


@pytest.mark.parametrize("stage", STAGES)
def test_an_unwired_stage_is_blocked_not_silently_ok(stage: str, capsys):
    assert main(["analyze", stage]) == 2
    assert stage in capsys.readouterr().out


def test_a_stage_the_contract_does_not_have_is_refused_by_the_parser():
    with pytest.raises(SystemExit) as refused:
        main(["analyze", "extract"])
    assert refused.value.code == 2
