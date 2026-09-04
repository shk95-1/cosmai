"""Whether the connection of the `ollama` factory survives passing the idle-in-transaction limit (#6/#21, a
defect found by measurement).

`OllamaPolarity` has no batch API and does a round trip per sentence (analysis/polarity/ollama.py:103) — when
the coordinating session ran the holdout as it was, it died on the first sentence with
`IdleInTransactionSessionTimeout`. Having inherited the shape of `_predictor` (llm) as it was, the
transaction `connect_lexicon()` opened stays open after the lexicon load, and while a round trip is waited out
inside it needs_runtime's idle_in_transaction_session_timeout (15s) is passed. The `llm` path has been
avoiding this as a side effect because `pricing.reserve()` commits before the API call — the free path has no
`reserve()` and so no such protection. Here that limit is compressed to 200ms to reproduce and verify it
within seconds (following the A-8-1 form of tests/collectors/commerce/test_source_lock.py).
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy.engine import make_url

from analysis import predictors
from analysis.polarity import predictor as polarity_predictor
from analysis.polarity.ollama import OllamaPolarity
from analysis.types import LabeledRow, PolarityResult
from db import seed
from db.seed._common import connect

# The two real limits of needs_runtime (db/bootstrap.sql) compressed so they are passed within seconds — the
# real 15s is not waited out.
SQUEEZED_TIMEOUTS = "-c idle_in_transaction_session_timeout=200ms -c transaction_timeout=400ms"
IDLE_KILL_MARGIN_S = 0.8  # 4x the squeezed idle limit above
EFFECTIVE_IDLE_TIMEOUT = "SELECT current_setting('idle_in_transaction_session_timeout')"


def _squeezed_url(base_url: str) -> str:
    url = make_url(base_url)
    existing = url.query.get("options", "")
    return url.update_query_dict({"options": f"{existing} {SQUEEZED_TIMEOUTS}".strip()}).render_as_string(
        hide_password=False
    )


@pytest.mark.postgres
def test_a_slow_local_predict_survives_the_squeezed_idle_in_transaction_timeout(
    needs_runtime_url: str, monkeypatch: pytest.MonkeyPatch
):
    seed.run_all(needs_runtime_url, only=("lexicon",))
    squeezed = _squeezed_url(needs_runtime_url)
    monkeypatch.setattr(predictors, "LEXICON_URL", squeezed)

    # Confirm the compression actually took first — otherwise the time assertion below passes for free.
    with connect(squeezed) as probe:
        row = probe.execute(EFFECTIVE_IDLE_TIMEOUT).fetchone()
        assert row is not None and row[0] == "200ms"

    def slow_classify_many(
        self: OllamaPolarity, items: list[object], aspects: object
    ) -> list[PolarityResult]:
        del aspects
        time.sleep(IDLE_KILL_MARGIN_S)  # dies here if the connection stays in a transaction while waiting
        return [
            PolarityResult(aspect=None, polarity="중립", reason="stub", version=self.version) for _ in items
        ]

    monkeypatch.setattr(OllamaPolarity, "classify_many", slow_classify_many)

    rows = [LabeledRow(task="polarity", ref="sun:1", split="holdout", gold="중립", text="괜찮아요", extra={})]
    predict = polarity_predictor._ollama_predictor("gemma4:latest")
    assert predict(rows) == ["중립"]
