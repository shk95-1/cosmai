"""Claude API polarity implementation — the same signature as the rules (analysis/polarity/__init__.py) and
the same 400 sentences.

The only path that spends money. Every call goes out after a reservation (reserve) in UsageLedger, and once
the response arrives the same row is settled (settle) against the measurement — even when no response comes
the reservation stays in the ledger and comes off the next run's budget. The output is pinned to
{aspect, polarity, reason} by structured output (`output_config.format`), and an answer outside the three
labels is asked once more and then folded to NEUTRAL (issue #6).
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from typing import Any

from analysis.polarity.pricing import Usage, UsageLedger
from analysis.polarity.prompt import aspect_names, system_prompt, user_prompt
from analysis.types import AspectLexicon, PolarityRequest, PolarityResult

API_KEY = "CLAUDE_API_KEY"  # contracts/secrets.md · not the SDK's default env name; the code passes it
DEFAULT_MODEL = "claude-opus-5"
# This date changes when the prompt changes — the version string is the prompt's revision
# (contracts/versioning.md).
PROMPT_DATE = "20260824"
POLARITIES = ("불만", "만족", "중립")
NEUTRAL = "중립"
# The two constants have different jobs. MAX_TOKENS is the API ceiling and thinking tokens come out of it
# (Opus 5 has it on by default) — too narrow and the answer is truncated, which spreads into a full-price
# single retry per batch item. ESTIMATED_OUTPUT_TOKENS is the output term of the reservation estimate.
# Serving both from one constant fires the hard stop early every time the ceiling is raised.
MAX_TOKENS = 4096
ESTIMATED_OUTPUT_TOKENS = 400
MAX_RETRIES = 2  # The same as the SDK default, spelled out because this path spends money
REASON_MAX = 200
# Estimation factor. count_tokens is a paid round trip too, so nothing is measured and the estimate is
# generous — one Korean character is often more than one token, and a reservation below the measurement
# makes the hard stop fire late.
ESTIMATED_TOKENS_PER_CHAR = 2
EFFORT = "low"  # No reason to use high to classify a single sentence (claude-api skill §Thinking & Effort)
POLL_SECONDS = 20.0
BATCH_TIMEOUT_SECONDS = 86400.0  # The maximum of the Batches contract. Cutting at an hour drops a paid batch
BATCH_RESULT_DAYS = 29  # How long results stay on the API — batch_id is the address they are collected from
CUSTOM_ID = "p{}"
NO_ASPECT = ""
OUT_OF_LABEL = "llm:재시도 후에도 라벨 밖"
TRUNCATED = "llm:max_tokens 로 잘림"
NO_RESULT = "llm:배치 결과 없음"
COUNT_KINDS = ("succeeded", "errored", "expired", "canceled", "processing")

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        # Empty string instead of nullable: no type union is expected of the json_schema subset
        # (B8 is a '' sentinel as well).
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
    """Structured output guarantees the first text block is valid JSON — thinking blocks are skipped."""
    return next((block.text for block in message.content if block.type == "text"), "")


def parse_answer(text: str, aspects: AspectLexicon, version: str) -> PolarityResult | None:
    """None when it is outside the three labels or not JSON — the caller picks a retry or a fold to
    NEUTRAL."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("polarity") not in POLARITIES:
        return None
    aspect = str(data.get("aspect") or NO_ASPECT).strip()
    return PolarityResult(
        # A name not in the lexicon was invented by the model — it must not grow the need_key vocabulary.
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
    """How many were succeeded/errored/expired/canceled — the tally of why rows became NEUTRAL (it is kept
    in the ledger)."""
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
        # ledger has no default: this class is the only place in the repo that spends money, and a call
        # without a ledger is a call without a hard stop.
        self.model = model
        self.ledger = ledger
        self.purpose = purpose
        self.batch = batch
        self.poll_seconds = poll_seconds
        self.timeout_seconds = timeout_seconds
        self.version = version_for(model, prompt_date)
        self._client = client

    # ---------- plumbing ----------
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
                    # 400 sentences share this system prompt. Under 1024 tokens the API ignores it silently.
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
        """The answer, and when there is none the reason for it. Truncation and a label violation are
        different events."""
        found = parse_answer(answer_text(message), aspects, self.version)
        if found is not None:
            return found, ""
        return None, TRUNCATED if getattr(message, "stop_reason", "") == "max_tokens" else OUT_OF_LABEL

    # ---------- single call (synchronous, for experiments) ----------
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
            found, why = self._ask(params, aspects)  # Contract: one retry when the answer is out of label
        return found or self._neutral(why or OUT_OF_LABEL)

    # ---------- batch (Batches API, two phases) ----------
    def submit(self, items: Sequence[PolarityRequest], aspects: AspectLexicon) -> str:
        requests = [
            # The SDK's Request · MessageCreateParamsNonStreaming are TypedDicts, so a plain dict is the same.
            {
                "custom_id": CUSTOM_ID.format(i),
                "params": self.params(x.sentence, x.rating, x.category, aspects),
            }
            for i, x in enumerate(items)
        ]
        estimate = total_usage([self.estimate(r["params"]) for r in requests])
        reservation = self.ledger.reserve(self.model, self.purpose, estimate, batch=True)
        # The reservation is kept even when create() throws: whether the batch was really created is unknown,
        # and erring towards "it was not" leaves a billed batch out of the budget.
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
        if reservation is None:  # Another run settled it, or there was none — record the measurement anyway
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
            if kind != "succeeded":  # errored | expired | canceled — which one is kept on the result row
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
            if key not in answers:  # failed · expired · canceled · missing: fold to NEUTRAL, no resubmit
                out.append(self._neutral(why.get(key, NO_RESULT)))
                continue
            # Only out-of-label or truncated items go once more as a single call (contract: one retry).
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
