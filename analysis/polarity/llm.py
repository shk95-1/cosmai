"""Claude API 극성 구현 — 규칙(analysis/polarity/__init__.py)과 같은 시그니처, 같은 400문장.

돈이 나가는 유일한 경로다. 모든 호출은 UsageLedger 의 예약(reserve)을 지난 뒤에 나가고, 응답이 오면
같은 행을 실측으로 정산(settle)한다 — 응답이 오지 않아도 예약분이 원장에 남아 다음 실행의 $7 에서
빠진다. 출력은 structured output(`output_config.format`)으로 {aspect, polarity, reason} 에 고정하고,
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
# 두 상수는 역할이 다르다. MAX_TOKENS 는 API 상한이고 thinking 토큰이 여기서 나간다(Opus 5 는 기본 on) —
# 좁으면 답이 잘려 배치 항목마다 정가 단건 재시도로 번진다. ESTIMATED_OUTPUT_TOKENS 는 예약 견적의
# 출력 항이다. 한 상수로 겸하면 상한을 올릴 때마다 하드스톱이 조기 발동한다.
MAX_TOKENS = 4096
ESTIMATED_OUTPUT_TOKENS = 400
MAX_RETRIES = 2  # SDK 기본값과 같지만 돈 경로라 명시한다
REASON_MAX = 200
# 견적용 계수. count_tokens 도 유료 왕복이라 재지 않고 넉넉히 잡는다 — 한글은 한 글자가 한 토큰을
# 넘는 일이 흔하고, 예약이 실측보다 작으면 하드스톱이 늦게 걸린다.
ESTIMATED_TOKENS_PER_CHAR = 2
EFFORT = "low"  # 한 문장 분류에 high 를 쓸 이유가 없다 (claude-api 스킬 §Thinking & Effort)
POLL_SECONDS = 20.0
BATCH_TIMEOUT_SECONDS = 86400.0  # Batches 계약의 최대치. 1시간으로 끊으면 청구된 배치를 버리게 된다
BATCH_RESULT_DAYS = 29  # 결과가 API 에 남는 기간 — batch_id 가 그 회수 주소다
CUSTOM_ID = "p{}"
NO_ASPECT = ""
OUT_OF_LABEL = "llm:재시도 후에도 라벨 밖"
TRUNCATED = "llm:max_tokens 로 잘림"
NO_RESULT = "llm:배치 결과 없음"
COUNT_KINDS = ("succeeded", "errored", "expired", "canceled", "processing")

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


def total_usage(parts: Sequence[Usage]) -> Usage:
    return Usage(
        input_tokens=sum(u.input_tokens for u in parts),
        output_tokens=sum(u.output_tokens for u in parts),
        cache_read=sum(u.cache_read for u in parts),
        cache_write=sum(u.cache_write for u in parts),
    )


def counts_note(counts: Any) -> str:
    """몇 건이 succeeded/errored/expired/canceled 였는지 — 중립이 된 이유의 집계다 (원장에 남는다)."""
    if counts is None:
        return ""
    seen = [(k, getattr(counts, k, None)) for k in COUNT_KINDS]
    return " batch[" + " ".join(f"{k}:{v}" for k, v in seen if v is not None) + "]"


class LLMPolarity:
    def __init__(
        self,
        model: str,
        ledger: UsageLedger,
        *,
        client: Any | None = None,
        purpose: str = "probe",
        prompt_date: str = PROMPT_DATE,
        batch: bool = True,
        poll_seconds: float = POLL_SECONDS,
        timeout_seconds: float = BATCH_TIMEOUT_SECONDS,
    ) -> None:
        # ledger 는 기본값이 없다: 이 클래스가 레포에서 돈을 쓰는 유일한 자리이고, 원장 없는 호출은
        # 하드스톱도 없는 호출이다.
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

            self._client = anthropic.Anthropic(
                api_key=secrets.require([API_KEY])[API_KEY], max_retries=MAX_RETRIES
            )
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
        return Usage(
            input_tokens=len(text) * ESTIMATED_TOKENS_PER_CHAR, output_tokens=ESTIMATED_OUTPUT_TOKENS
        )

    def _neutral(self, reason: str) -> PolarityResult:
        return PolarityResult(aspect=None, polarity=NEUTRAL, reason=reason, version=self.version)

    def _read(self, message: Any, aspects: AspectLexicon) -> tuple[PolarityResult | None, str]:
        """답과, 답이 없을 때 그 이유. 잘림과 라벨 위반은 다른 사건이다."""
        found = parse_answer(answer_text(message), aspects, self.version)
        if found is not None:
            return found, ""
        return None, TRUNCATED if getattr(message, "stop_reason", "") == "max_tokens" else OUT_OF_LABEL

    # ---------- 단건 (동기, 실험용) ----------
    def _ask(self, params: dict[str, Any], aspects: AspectLexicon) -> tuple[PolarityResult | None, str]:
        reservation = self.ledger.reserve(self.model, self.purpose, self.estimate(params))
        message = self.client.messages.create(**params)
        self.ledger.settle(reservation, self.purpose, usage_of(message.usage))
        return self._read(message, aspects)

    def classify(
        self, sentence: str, rating: float | None, category: str | None, aspects: AspectLexicon
    ) -> PolarityResult:
        params = self.params(sentence, rating, category, aspects)
        found, why = self._ask(params, aspects)
        if found is None:
            found, why = self._ask(params, aspects)  # 계약: 라벨 밖이면 재시도 1회
        return found or self._neutral(why or OUT_OF_LABEL)

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
        estimate = total_usage([self.estimate(r["params"]) for r in requests])
        reservation = self.ledger.reserve(self.model, self.purpose, estimate, batch=True)
        # create() 가 던져도 예약은 남긴다: 배치가 실제로 만들어졌는지 알 수 없고, 없는 쪽으로 틀리면
        # 청구된 배치가 예산에서 빠지지 않는다.
        batch_id = str(self.client.messages.batches.create(requests=requests).id)
        self.ledger.attach_batch_id(reservation, batch_id)
        return batch_id

    def wait(self, batch_id: str) -> Any:
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            batch = self.client.messages.batches.retrieve(batch_id)
            if batch.processing_status == "ended":
                return batch
            if time.monotonic() >= deadline:
                raise LookupError(
                    f"batch {batch_id} is still running after {self.timeout_seconds:.0f}s; its results "
                    f"stay on the API for {BATCH_RESULT_DAYS} days -- collect() with that id, and "
                    "needs.llm_usage already holds the reservation under the same batch_id"
                )
            time.sleep(self.poll_seconds)

    def _settle_batch(self, batch_id: str, usage: Usage, counts: Any) -> None:
        purpose = self.purpose + counts_note(counts)
        reservation = self.ledger.reservation_for(batch_id)
        if reservation is None:  # 다른 실행이 이미 정산했거나 예약이 없었다 — 실측만이라도 남긴다
            self.ledger.record(self.model, purpose, usage, batch=True, batch_id=batch_id)
            return
        self.ledger.settle(reservation, purpose, usage, batch_id=batch_id)

    def collect(
        self,
        batch_id: str,
        items: Sequence[PolarityRequest],
        aspects: AspectLexicon,
        counts: Any = None,
    ) -> list[PolarityResult]:
        answers: dict[str, PolarityResult | None] = {}
        why: dict[str, str] = {}
        spent: list[Usage] = []
        for result in self.client.messages.batches.results(batch_id):
            custom_id = str(result.custom_id)
            kind = str(result.result.type)
            if kind != "succeeded":  # errored | expired | canceled — 어느 쪽인지 결과 행에 남긴다
                why[custom_id] = f"llm:배치 {kind}"
                continue
            message = result.result.message
            spent.append(usage_of(message.usage))
            answers[custom_id], why[custom_id] = self._read(message, aspects)
        self._settle_batch(batch_id, total_usage(spent), counts)
        out: list[PolarityResult] = []
        for i, item in enumerate(items):
            key = CUSTOM_ID.format(i)
            found = answers.get(key)
            if found is not None:
                out.append(found)
                continue
            if key not in answers:  # 실패·만료·취소·누락: 배치 전체를 다시 내지 않고 중립으로 접는다
                out.append(self._neutral(why.get(key, NO_RESULT)))
                continue
            # 라벨 밖이거나 잘린 건만 단건으로 한 번 더 (계약: 재시도 1회).
            params = self.params(item.sentence, item.rating, item.category, aspects)
            retried, reason = self._ask(params, aspects)
            out.append(retried or self._neutral(reason or why.get(key, OUT_OF_LABEL)))
        return out

    def classify_many(self, items: Sequence[PolarityRequest], aspects: AspectLexicon) -> list[PolarityResult]:
        if not items:
            return []
        if not self.batch:
            return [self.classify(x.sentence, x.rating, x.category, aspects) for x in items]
        batch_id = self.submit(items, aspects)
        batch = self.wait(batch_id)
        return self.collect(batch_id, items, aspects, counts=getattr(batch, "request_counts", None))
