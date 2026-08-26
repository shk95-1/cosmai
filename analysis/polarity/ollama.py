"""ollama 배관 — 같은 프롬프트·같은 스키마를 로컬 모델에 보낸다. 채택 판정에는 쓰지 않는다.

돈이 들지 않으므로 오프라인 스위트가 못 만지는 코드 경로(프롬프트 조립·JSON 파싱·재시도)를 여기서
왕복시킨다. 그래서 `local_llm` 마커이고 기본 제외다 (pyproject·tests/conftest.py).
"""

from __future__ import annotations

import http.client
import json
import os
import urllib.request
from collections.abc import Sequence
from typing import Any

from analysis.polarity.fewshot import FEWSHOT_TAG, shots_for
from analysis.polarity.llm import (
    NEUTRAL,
    SCHEMA,
    parse_answer,
    version_for,
)
from analysis.polarity.pricing import Usage, UsageLedger
from analysis.polarity.prompt import system_prompt, user_prompt
from analysis.types import AspectLexicon, PolarityRequest, PolarityResult

OLLAMA_URL_KEY = "OLLAMA_URL"  # secret 이 아니라 주소 노브다 (stack/env.example, contracts/secrets.md)
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "gemma4:latest"  # 2026-08-23 /api/tags 확인 (8B Q4_K_M)
CHAT_PATH = "/api/chat"
TAGS_PATH = "/api/tags"
# 프롬프트 판본은 Claude 경로(llm.PROMPT_DATE)와 갈라져 있다: few-shot 은 여기에만 걸리므로 공유
# 상수를 쓰면 interfaces.md 에 이미 기록된 Sonnet/Opus 숫자와 같은 판본을 주장하게 된다.
OLLAMA_PROMPT_DATE = "20260824"
# gemma4 의 사고 토큰은 호출당 6~9s 인데 eval_count 에 안 잡힌다 — few-shot 이 그 자리를 대신한다.
THINK = False
TIMEOUT_SECONDS = 180.0
# 프로브는 생성을 시키지 않으므로 모델 적재를 기다리지 않는다 — 이 초는 TCP 왕복 하나의 값이다.
PROBE_TIMEOUT_SECONDS = 10.0
LEDGER_PREFIX = "ollama:"


def ollama_url() -> str:
    return os.environ.get(OLLAMA_URL_KEY) or DEFAULT_OLLAMA_URL


def chat_payload(
    model: str, sentence: str, rating: float | None, category: str | None, aspects: AspectLexicon
) -> dict[str, Any]:
    return {
        "model": model,
        "stream": False,
        "think": THINK,
        "format": SCHEMA,  # ollama 의 구조화 출력도 같은 JSON 스키마를 받는다
        "options": {"temperature": 0},
        "messages": [
            {"role": "system", "content": system_prompt(aspects)},
            *shots_for(aspects.ruleset),
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
        prompt_date: str = OLLAMA_PROMPT_DATE,
        timeout_seconds: float = TIMEOUT_SECONDS,
    ) -> None:
        self.model = model
        self.ledger = ledger
        self.base_url = (base_url or ollama_url()).rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.version = version_for(f"ollama-{model}-{FEWSHOT_TAG}", prompt_date)

    def preflight(self) -> None:
        """이 주소가 열려 있고 이 모델이 거기 있는가 — 단계가 시작 직후 한 번 묻는다 (#32).

        조용한 실패가 이 배선이 세 번 밟은 병이다: 프로브가 없으면 못 닿은 밤도 첫 배치까지 가서야
        죽고, 그 죽음은 `--missing` 이 rewriting 표식을 안 달아 아무도 되짚지 않는 자리에 떨어진다.
        생성은 시키지 않는다 — 첫 왕복은 모델 재적재를 끼면 21s 이고, 그 시간은 프로브가 아니라 첫
        배치가 쓸 값이다. 대신 왕복 고장을 `LookupError` 로 바꿔 준다: 그것이 단계가 잡아 run 을
        failed 로 닫는 예외다 (analysis/pipeline.py 의 FAILURES).
        """
        request = urllib.request.Request(self.base_url + TAGS_PATH)
        try:
            with urllib.request.urlopen(request, timeout=PROBE_TIMEOUT_SECONDS) as response:  # noqa: S310
                body = json.loads(response.read().decode())
            served = sorted(str(m.get("model") or m.get("name") or "") for m in body.get("models") or [])
        except (OSError, http.client.HTTPException, ValueError) as unreachable:
            raise LookupError(
                f"ollama at {self.base_url} did not answer {TAGS_PATH} "
                f"({type(unreachable).__name__}: {unreachable}); {OLLAMA_URL_KEY} names that address"
            ) from unreachable
        if self.model not in served:
            # 포트가 열려 있다고 그 모델이 거기 있는 것은 아니다 — 가진 것을 같이 적어야 오타가 보인다.
            raise LookupError(
                f"ollama at {self.base_url} serves no model {self.model!r}; it has "
                f"{', '.join(served) or 'none'} ({OLLAMA_URL_KEY} names that address)"
            )

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
