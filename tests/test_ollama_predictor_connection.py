"""`ollama` 팩터리의 커넥션이 idle-in-transaction 한도를 넘기고 살아남는지 (#6/#21 실측 결함).

`OllamaPolarity` 는 배치 API 가 없어 문장마다 왕복한다(analysis/polarity/ollama.py:103) — 조정 세션이
그대로 홀드아웃을 돌리자 첫 문장에서 `IdleInTransactionSessionTimeout` 으로 죽었다. `_predictor`(llm)
의 모양을 그대로 물려받아 `connect_lexicon()` 이 연 트랜잭션이 사전 로드 이후에도 열린 채 남고, 그
안에서 왕복을 기다리는 동안 needs_runtime 의 idle_in_transaction_session_timeout(15s) 이 넘어간다.
`llm` 경로는 `pricing.reserve()` 가 API 호출 전에 커밋해 부수적으로 피해 왔다 — 무료 경로엔 `reserve()`
가 없어 그 보호가 없다. 여기서는 그 한도를 200ms 로 압축해 몇 초 안에 재현·검증한다
(tests/collectors/commerce/test_source_lock.py 의 A-8-1 형식을 따른다).
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

# needs_runtime 의 실제 두 한도(db/bootstrap.sql)를 몇 초 안에 넘기도록 압축한 것 — 실제 15s 를 기다리지
# 않는다.
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

    # 압축이 실제로 먹었는지 먼저 확인한다 — 안 그러면 아래 시간 단언이 공짜로 통과한다.
    with connect(squeezed) as probe:
        row = probe.execute(EFFECTIVE_IDLE_TIMEOUT).fetchone()
        assert row is not None and row[0] == "200ms"

    def slow_classify_many(
        self: OllamaPolarity, items: list[object], aspects: object
    ) -> list[PolarityResult]:
        del aspects
        time.sleep(IDLE_KILL_MARGIN_S)  # 왕복을 기다리는 동안 커넥션이 트랜잭션에 남으면 여기서 죽는다
        return [
            PolarityResult(aspect=None, polarity="중립", reason="stub", version=self.version) for _ in items
        ]

    monkeypatch.setattr(OllamaPolarity, "classify_many", slow_classify_many)

    rows = [LabeledRow(task="polarity", ref="sun:1", split="holdout", gold="중립", text="괜찮아요", extra={})]
    predict = polarity_predictor._ollama_predictor("gemma4:latest")
    assert predict(rows) == ["중립"]
