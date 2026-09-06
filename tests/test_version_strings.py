"""Every `*_version` string this repo builds has to be one of the two formats of contracts/versioning.md.

A rule written only in prose breaks -- the `rule-v1` of #2 passed quietly with no minor. A unit adds its own
constant to VERSIONS as one line.
"""

from __future__ import annotations

import re

import pytest

from analysis.aggregate import AGGREGATE_VERSION
from analysis.extractor import VERSION as EXTRACTOR_VERSION
from analysis.linker import LINKER_VERSION
from analysis.polarity import VERSION as POLARITY_VERSION
from analysis.polarity.llm import VERSION as LLM_POLARITY_VERSION
from analysis.polarity.ownership import _GEMMA4_2026_08_24, OWNERS

# versioning.md: `rule-vX.Y` or `llm-<model>-<yyyymmdd>`.
FORMAT = re.compile(r"^rule-v\d+\.\d+$|^llm-.+-\d{8}$")

VERSIONS = (
    ("analysis.linker.LINKER_VERSION", LINKER_VERSION),
    ("analysis.extractor.VERSION", EXTRACTOR_VERSION),
    ("analysis.polarity.VERSION", POLARITY_VERSION),
    ("analysis.aggregate.AGGREGATE_VERSION", AGGREGATE_VERSION),
    ("analysis.polarity.llm.VERSION", LLM_POLARITY_VERSION),
    # OllamaPolarity(...).version is an instance attribute, so the constant cannot be imported
    # (analysis/polarity/ollama.py) -- the value production stamps is _GEMMA4_2026_08_24, the
    # re-registration constant OWNERS is suspended empty from as of #242.
    ("analysis.polarity.ownership._GEMMA4_2026_08_24", _GEMMA4_2026_08_24),
)


@pytest.mark.parametrize(("name", "version"), VERSIONS)
def test_a_version_constant_matches_one_of_the_two_contract_shapes(name: str, version: str):
    assert FORMAT.match(version), f"{name} = {version!r}"


def test_the_guard_refuses_the_shapes_the_contract_does_not_have():
    assert not FORMAT.match("rule-v1")
    assert not FORMAT.match("slice-p2")
    assert not FORMAT.match("llm-sonnet-2026-08-23")
    assert FORMAT.match("llm-claude-sonnet-4-20260823")


def test_every_operational_owner_version_is_registered_in_versions():
    """Vacuous while OWNERS is suspended empty (#242) -- it re-arms the moment an entry comes back."""
    registered = {version for _, version in VERSIONS}
    for scope, owner in OWNERS.items():
        assert FORMAT.match(owner.version), f"{scope} = {owner.version!r}"
        assert owner.version in registered, f"{scope} = {owner.version!r} missing from VERSIONS"
