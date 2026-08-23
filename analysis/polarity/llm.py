"""Claude API 극성 구현 — 규칙(analysis/polarity/__init__.py)과 같은 시그니처, 같은 400문장.

돈이 나가는 유일한 경로다. 모든 호출은 UsageLedger 를 지나고 하드스톱은 호출 전에 걸린다.
출력은 structured output(`output_config.format`)으로 {aspect, polarity, reason} 에 고정하고,
세 라벨 밖의 답은 한 번 더 물은 뒤 중립으로 접는다 (이슈 #6).
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from typing import Any

from analysis.polarity.pricing import Usage, UsageLedger
from analysis.polarity.prompt import aspect_names, system_prompt, user_prompt
from analysis.types import AspectLexicon, PolarityRequest, PolarityResult

API_KEY = "CLAUDE_API_KEY"  # contracts/secrets.md · SDK 기본 env 이름이 아니라 코드가 명시로 넘긴다
DEFAULT_MODEL = "claude-opus-5"
# 프롬프트가 바뀌면 이 날짜가 바뀐다 — 버전 문자열이 곧 프롬프트의 판본이다 (contracts/versioning.md).
PROMPT_DATE = "20260824"
POLARITIES = ("불만", "만족", "중립")
NEUTRAL = "중립"
MAX_TOKENS = 1024  # 한 문장 판정 + 짧은 근거. 견적의 출력 상한이기도 하다
EFFORT = "low"  # 한 문장 분류에 high 를 쓸 이유가 없다 (claude-api 스킬 §Thinking & Effort)
REASON_MAX = 200
CHARS_PER_TOKEN = 1  # 견적용 보수적 계수: count_tokens 도 유료 왕복이라 한 글자 = 한 토큰으로 잡는다
POLL_SECONDS = 20.0
BATCH_TIMEOUT_SECONDS = 3600.0  # 대부분 1시간 안에 끝난다 (Batches 계약)
CUSTOM_ID = "p{}"
NO_ASPECT = ""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        # nullable 대신 빈 문자열: json_schema 서브셋에 타입 합집합을 기대지 않는다 (B8 도 '' 센티널이다).
        "aspect": {"type": "string"},
        "polarity": {"type": "string", "enum": list(POLARITIES)},
        "reason": {"type": "string"},
    },
    "required": ["aspect", "polarity", "reason"],
    "additionalProperties": False,
}


def version_for(model: str, prompt_date: str = PROMPT_DATE) -> str:
    return f"llm-{model}-{prompt_date}"


VERSION = version_for(DEFAULT_MODEL)


def answer_text(message: Any) -> str:
    """structured output 은 첫 text 블록이 유효한 JSON 임을 보장한다 — thinking 블록은 건너뛴다."""
    return next((block.text for block in message.content if block.type == "text"), "")


def parse_answer(text: str, aspects: AspectLexicon, version: str) -> PolarityResult | None:
    """세 라벨 밖이거나 JSON 이 아니면 None — 부르는 쪽이 재시도할지 중립으로 접을지 정한다."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("polarity") not in POLARITIES:
        return None
    aspect = str(data.get("aspect") or NO_ASPECT).strip()
    return PolarityResult(
        # 사전에 없는 이름은 모델이 지어낸 것이다 — need_key 어휘를 늘리지 않는다.
        aspect=aspect if aspect in aspect_names(aspects) else None,
        polarity=str(data["polarity"]),
        reason=str(data.get("reason") or "")[:REASON_MAX],
        version=version,
    )


def usage_of(usage: Any) -> Usage:
    return Usage(
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        cache_read=getattr(usage, "cache_read_input_tokens", 0) or 0,
        cache_write=getattr(usage, "cache_creation_input_tokens", 0) or 0,
    )


class LLMPolarity:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        ledger: UsageLedger | None = None,
        *,
        client: Any | None = None,
        purpose: str = "probe",
        prompt_date: str = PROMPT_DATE,
        batch: bool = True,
        poll_seconds: float = POLL_SECONDS,
        timeout_seconds: float = BATCH_TIMEOUT_SECONDS,
    ) -> None:
        self.model = model
        self.ledger = ledger
        self.purpose = purpose
        self.batch = batch
        self.poll_seconds = poll_seconds
        self.timeout_seconds = timeout_seconds
        self.version = version_for(model, prompt_date)
        self._client = client

    # ---------- 배관 ----------
    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic

            from db import secrets

            self._client = anthropic.Anthropic(api_key=secrets.require([API_KEY])[API_KEY])
        return self._client

    def params(
        self, sentence: str, rating: float | None, category: str | None, aspects: AspectLexicon
    ) -> dict[str, Any]:
        return {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": [
                {
                    "type": "text",
                    "text": system_prompt(aspects),
                    # 400문장이 같은 시스템 프롬프트를 쓴다. 1024토큰 미만이면 API 가 조용히 무시한다.
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}},
            "messages": [{"role": "user", "content": user_prompt(sentence, rating, category)}],
        }

    def estimate(self, params: dict[str, Any]) -> Usage:
        text = params["system"][0]["text"] + params["messages"][0]["content"]
        return Usage(input_tokens=len(text) // CHARS_PER_TOKEN, output_tokens=MAX_TOKENS)

    def _spend(self, usage: Any, *, batch: bool = False, batch_id: str | None = None) -> None:
        if self.ledger is not None:
            self.ledger.record(self.model, self.purpose, usage_of(usage), batch=batch, batch_id=batch_id)

    def _neutral(self, reason: str) -> PolarityResult:
        return PolarityResult(aspect=None, polarity=NEUTRAL, reason=reason, version=self.version)

    # ---------- 단건 (동기, 실험용) ----------
    def _ask(self, params: dict[str, Any], aspects: AspectLexicon) -> PolarityResult | None:
        if self.ledger is not None:
            self.ledger.check(self.model, self.estimate(params))  # 호출 *전* 하드스톱
        message = self.client.messages.create(**params)
        self._spend(message.usage)
        return parse_answer(answer_text(message), aspects, self.version)

    def classify(
        self, sentence: str, rating: float | None, category: str | None, aspects: AspectLexicon
    ) -> PolarityResult:
        params = self.params(sentence, rating, category, aspects)
        found = self._ask(params, aspects)
        if found is None:
            found = self._ask(params, aspects)  # 계약: 라벨 밖이면 재시도 1회
        return found or self._neutral("llm:재시도 후에도 라벨 밖")

    # ---------- 배치 (Batches API, 2상) ----------
    def submit(self, items: Sequence[PolarityRequest], aspects: AspectLexicon) -> str:
        requests = [
            # SDK 의 Request·MessageCreateParamsNonStreaming 은 TypedDict 라 평범한 dict 와 같다.
            {
                "custom_id": CUSTOM_ID.format(i),
                "params": self.params(x.sentence, x.rating, x.category, aspects),
            }
            for i, x in enumerate(items)
        ]
        if self.ledger is not None:
            estimate = self.estimate(requests[0]["params"])
            self.ledger.check(
                self.model,
                Usage(
                    input_tokens=estimate.input_tokens * len(requests),
                    output_tokens=estimate.output_tokens * len(requests),
                ),
                batch=True,
            )
        return str(self.client.messages.batches.create(requests=requests).id)

    def wait(self, batch_id: str) -> None:
        deadline = time.monotonic() + self.timeout_seconds
        while self.client.messages.batches.retrieve(batch_id).processing_status != "ended":
            if time.monotonic() >= deadline:
                raise LookupError(f"batch {batch_id} is still running after {self.timeout_seconds:.0f}s")
            time.sleep(self.poll_seconds)

    def collect(
        self, batch_id: str, items: Sequence[PolarityRequest], aspects: AspectLexicon
    ) -> list[PolarityResult]:
        answers: dict[str, PolarityResult | None] = {}
        for result in self.client.messages.batches.results(batch_id):
            if result.result.type != "succeeded":
                continue
            message = result.result.message
            self._spend(message.usage, batch=True, batch_id=batch_id)
            answers[str(result.custom_id)] = parse_answer(answer_text(message), aspects, self.version)
        out: list[PolarityResult] = []
        for i, item in enumerate(items):
            key = CUSTOM_ID.format(i)
            # 실패·만료·취소한 건: 재시도가 배치 전체를 다시 내는 일이라 중립으로 접는다.
            if key not in answers:
                out.append(self._neutral("llm:배치 결과 없음"))
                continue
            found = answers[key]
            if found is None:  # 라벨 밖이면 단건으로 한 번 더 (계약: 재시도 1회)
                found = self._ask(self.params(item.sentence, item.rating, item.category, aspects), aspects)
            out.append(found or self._neutral("llm:재시도 후에도 라벨 밖"))
        return out

    def classify_many(self, items: Sequence[PolarityRequest], aspects: AspectLexicon) -> list[PolarityResult]:
        if not items:
            return []
        if not self.batch:
            return [self.classify(x.sentence, x.rating, x.category, aspects) for x in items]
        batch_id = self.submit(items, aspects)
        self.wait(batch_id)
        return self.collect(batch_id, items, aspects)
