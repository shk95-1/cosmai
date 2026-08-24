"""ollama 배관 — 같은 프롬프트·같은 스키마를 로컬 모델에 보낸다. 채택 판정에는 쓰지 않는다.

돈이 들지 않으므로 오프라인 스위트가 못 만지는 코드 경로(프롬프트 조립·JSON 파싱·재시도)를 여기서
왕복시킨다. 그래서 `local_llm` 마커이고 기본 제외다 (pyproject·tests/conftest.py).
"""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Sequence
from typing import Any

from analysis.polarity.llm import (
    NEUTRAL,
    PROMPT_DATE,
    SCHEMA,
    parse_answer,
    version_for,
)
from analysis.polarity.pricing import Usage, UsageLedger
from analysis.polarity.prompt import system_prompt, user_prompt
from analysis.types import AspectLexicon, PolarityRequest, PolarityResult

OLLAMA_URL_KEY = "OLLAMA_URL"  # contracts/secrets.md
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "gemma4:latest"  # 2026-08-23 /api/tags 확인 (8B Q4_K_M)
CHAT_PATH = "/api/chat"
TIMEOUT_SECONDS = 180.0
LEDGER_PREFIX = "ollama:"


def ollama_url() -> str:
    return os.environ.get(OLLAMA_URL_KEY) or DEFAULT_OLLAMA_URL


def chat_payload(
    model: str, sentence: str, rating: float | None, category: str | None, aspects: AspectLexicon
) -> dict[str, Any]:
    return {
        "model": model,
        "stream": False,
        "format": SCHEMA,  # ollama 의 구조화 출력도 같은 JSON 스키마를 받는다
        "options": {"temperature": 0},
        "messages": [
            {"role": "system", "content": system_prompt(aspects)},
            {"role": "user", "content": user_prompt(sentence, rating, category)},
        ],
    }


class OllamaPolarity:
    def __init__(
        self,
        model: str = DEFAULT_OLLAMA_MODEL,
        ledger: UsageLedger | None = None,
        *,
        base_url: str | None = None,
        prompt_date: str = PROMPT_DATE,
        timeout_seconds: float = TIMEOUT_SECONDS,
    ) -> None:
        self.model = model
        self.ledger = ledger
        self.base_url = (base_url or ollama_url()).rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.version = version_for(f"ollama-{model}", prompt_date)

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            self.base_url + CHAT_PATH,
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
            return json.loads(response.read().decode())

    def _ask(self, payload: dict[str, Any], aspects: AspectLexicon) -> PolarityResult | None:
        body = self._post(payload)
        if self.ledger is not None:
            # 무료지만 원장에는 남긴다 — 어떤 모델이 몇 번 돌았는지가 배관 검증의 기록이다.
            self.ledger.record(
                LEDGER_PREFIX + self.model,
                "local_llm",
                Usage(
                    input_tokens=int(body.get("prompt_eval_count") or 0),
                    output_tokens=int(body.get("eval_count") or 0),
                ),
            )
        return parse_answer(str(body.get("message", {}).get("content", "")), aspects, self.version)

    def classify(
        self, sentence: str, rating: float | None, category: str | None, aspects: AspectLexicon
    ) -> PolarityResult:
        payload = chat_payload(self.model, sentence, rating, category, aspects)
        found = self._ask(payload, aspects) or self._ask(payload, aspects)
        return found or PolarityResult(
            aspect=None, polarity=NEUTRAL, reason="ollama:재시도 후에도 라벨 밖", version=self.version
        )

    def classify_many(self, items: Sequence[PolarityRequest], aspects: AspectLexicon) -> list[PolarityResult]:
        # ollama 에는 배치 API 가 없다 — 단건 반복이 곧 배치다.
        return [self.classify(x.sentence, x.rating, x.category, aspects) for x in items]
