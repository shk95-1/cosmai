"""이 레포가 만드는 `*_version` 문자열은 contracts/versioning.md 의 두 형식 중 하나여야 한다.

산문으로만 적힌 규칙은 깨진다 — #2 의 `rule-v1` 이 마이너 없이 조용히 통과했다. 유닛은 자기 상수를
VERSIONS 에 한 줄로 더한다.
"""

from __future__ import annotations

import re

import pytest

from analysis.aggregate import AGGREGATE_VERSION
from analysis.extractor import VERSION as EXTRACTOR_VERSION
from analysis.linker import LINKER_VERSION
from analysis.polarity import VERSION as POLARITY_VERSION
from analysis.polarity.llm import VERSION as LLM_POLARITY_VERSION

# versioning.md: `rule-vX.Y` 또는 `llm-<model>-<yyyymmdd>`.
FORMAT = re.compile(r"^rule-v\d+\.\d+$|^llm-.+-\d{8}$")

VERSIONS = (
    ("analysis.linker.LINKER_VERSION", LINKER_VERSION),
    ("analysis.extractor.VERSION", EXTRACTOR_VERSION),
    ("analysis.polarity.VERSION", POLARITY_VERSION),
    ("analysis.aggregate.AGGREGATE_VERSION", AGGREGATE_VERSION),
    ("analysis.polarity.llm.VERSION", LLM_POLARITY_VERSION),
)


@pytest.mark.parametrize(("name", "version"), VERSIONS)
def test_a_version_constant_matches_one_of_the_two_contract_shapes(name: str, version: str):
    assert FORMAT.match(version), f"{name} = {version!r}"


def test_the_guard_refuses_the_shapes_the_contract_does_not_have():
    assert not FORMAT.match("rule-v1")
    assert not FORMAT.match("slice-p2")
    assert not FORMAT.match("llm-sonnet-2026-08-23")
    assert FORMAT.match("llm-claude-sonnet-4-20260823")
