"""collectors/youtube/scope.json is the one place the fan-out caps and their rationale are written down
(issue #8 §산출물 2); models.py and queue.py must read the same numbers, not a hardcoded copy -- same
guard collectors/commerce's test_scope_matches_constants.py gives the ranking/review constants."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from collectors.youtube import models, queue

SCOPE_JSON = Path(__file__).resolve().parents[3] / "collectors" / "youtube" / "scope.json"


def test_scope_json_has_both_caps_as_positive_ints():
    on_disk = json.loads(SCOPE_JSON.read_text(encoding="utf-8"))
    for key in ("MAX_FOLLOWUPS_PER_VIDEO", "MAX_QUEUE_DEPTH"):
        value = on_disk[key]
        assert isinstance(value, int) and not isinstance(value, bool) and value > 0


def test_models_constants_are_scope_json_verbatim():
    on_disk = json.loads(SCOPE_JSON.read_text(encoding="utf-8"))
    assert models.MAX_FOLLOWUPS_PER_VIDEO == on_disk["MAX_FOLLOWUPS_PER_VIDEO"]
    assert models.MAX_QUEUE_DEPTH == on_disk["MAX_QUEUE_DEPTH"]


def test_queue_module_uses_the_same_constants_as_models():
    assert queue.MAX_FOLLOWUPS_PER_VIDEO == models.MAX_FOLLOWUPS_PER_VIDEO
    assert queue.MAX_QUEUE_DEPTH == models.MAX_QUEUE_DEPTH


def test_freshness_seconds_are_positive_ints_keyed_by_a_known_kind():
    on_disk = json.loads(SCOPE_JSON.read_text(encoding="utf-8"))
    seconds_by_kind = on_disk["FRESHNESS_SECONDS"]
    assert seconds_by_kind
    for kind, seconds in seconds_by_kind.items():
        assert isinstance(kind, str) and kind
        assert isinstance(seconds, int) and not isinstance(seconds, bool) and seconds > 0


def test_models_freshness_is_scope_json_verbatim_as_timedeltas():
    on_disk = json.loads(SCOPE_JSON.read_text(encoding="utf-8"))
    assert models.FRESHNESS == {k: timedelta(seconds=v) for k, v in on_disk["FRESHNESS_SECONDS"].items()}
