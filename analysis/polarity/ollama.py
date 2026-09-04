"""ollama plumbing — the same prompt and the same schema sent to a local model. Not used to decide adoption.

Because it costs nothing, the code paths the offline suite cannot touch (prompt assembly, JSON parsing,
retry) are exercised over a real round trip here. Hence the `local_llm` marker, excluded by default
(pyproject · tests/conftest.py).
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

OLLAMA_URL_KEY = "OLLAMA_URL"  # An address knob, not a secret (stack/env.example, contracts/secrets.md)
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "gemma4:latest"  # Checked against /api/tags on 2026-08-23 (8B Q4_K_M)
CHAT_PATH = "/api/chat"
TAGS_PATH = "/api/tags"
# The prompt revision is split from the Claude path (llm.PROMPT_DATE): few-shot applies only here, so a
# shared constant would claim the same revision as the Sonnet/Opus numbers already recorded in interfaces.md.
OLLAMA_PROMPT_DATE = "20260824"
# gemma4's thinking tokens cost 6-9s per call and are not counted in eval_count — few-shot takes their place.
THINK = False
TIMEOUT_SECONDS = 180.0
# The probe generates nothing, so it does not wait for the model to load — this is one TCP round trip.
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
        "format": SCHEMA,  # ollama's structured output takes the same JSON schema
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
        """Is this address open and is this model on it — the stage asks once right after it starts (#32).

        Silent failure is the illness this wiring caught three times: without a probe a night that never
        reached anything dies only at the first batch, and that death lands where `--missing` puts no
        rewriting mark and nobody traces it back. Nothing is generated — the first round trip is 21s once a
        model reload is in it, and that time belongs to the first batch rather than to the probe. What it
        does instead is turn a broken round trip into a `LookupError`: that is the exception the stage
        catches to close the run as failed (FAILURES in analysis/pipeline.py).
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
            # An open port does not mean the model is there — listing what it has makes a typo visible.
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
            # Free, but still recorded in the ledger — which model ran how often is the plumbing's record.
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
        # ollama has no batch API — looping over single calls is the batch.
        return [self.classify(x.sentence, x.rating, x.category, aspects) for x in items]
