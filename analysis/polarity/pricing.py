"""모델별 단가와 $7.00 하드스톱. 누적은 needs.llm_usage(DDL 003)이고 차단은 호출 *전*이다.

호출 후에 세면 이미 나간 돈은 돌아오지 않는다 — check() 가 남은 예산보다 큰 견적을 거절하고, record()
는 응답이 돌아온 뒤 실제 usage 로 한 행을 남기고 바로 커밋한다(중간에 죽어도 원장은 사실이다).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, LiteralString

import psycopg

# 출처: claude-api 스킬 §Current Models(캐시 2026-06-24)를 2026-08-24 에 읽은 값. $/1M 토큰.
# cache read = input x 0.1, cache write(5분 ephemeral) = input x 1.25 (같은 스킬 §Prompt Caching).
# Sonnet 5 는 2026-08-31 까지 인트로 $2/$10 이지만 하드스톱은 정가로 센다 — 늦게 막느니 일찍 막는다.
PRICES_SOURCE_DATE = "2026-08-24"
PER_MILLION = Decimal(1_000_000)
BATCH_DISCOUNT = Decimal("0.5")  # Batches API 는 모든 토큰이 50%
LLM_BUDGET_USD = Decimal("7.00")  # contracts/secrets.md · 이슈 #6 사전 승인
OLLAMA_PREFIX = "ollama:"


@dataclass(frozen=True)
class Price:
    input_usd: Decimal
    output_usd: Decimal
    cache_read_usd: Decimal
    cache_write_usd: Decimal


def _price(input_usd: str, output_usd: str) -> Price:
    rate = Decimal(input_usd)
    return Price(rate, Decimal(output_usd), rate / 10, rate * Decimal("1.25"))


PRICES: dict[str, Price] = {
    "claude-opus-5": _price("5.00", "25.00"),
    "claude-sonnet-5": _price("3.00", "15.00"),
    "claude-haiku-4-5": _price("1.00", "5.00"),
}
FREE = Price(Decimal(0), Decimal(0), Decimal(0), Decimal(0))


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0


class BudgetExceeded(RuntimeError):
    """하드스톱. 이 예외가 던져진 시점에 그 호출은 아직 나가지 않았다."""


def price_for(model: str) -> Price:
    if model.startswith(OLLAMA_PREFIX):
        return FREE  # 로컬 모델은 무료다 — 원장에는 남기되 예산은 먹지 않는다
    price = PRICES.get(model)
    if price is None:
        raise LookupError(
            f"{model} has no price in analysis/polarity/pricing.py (source {PRICES_SOURCE_DATE}); "
            "add its four rates before spending money on it"
        )
    return price


def cost_usd(model: str, usage: Usage, *, batch: bool = False) -> Decimal:
    price = price_for(model)
    total = (
        usage.input_tokens * price.input_usd
        + usage.output_tokens * price.output_usd
        + usage.cache_read * price.cache_read_usd
        + usage.cache_write * price.cache_write_usd
    ) / PER_MILLION
    return total * BATCH_DISCOUNT if batch else total


SPENT: LiteralString = "SELECT coalesce(sum(usd), 0) FROM llm_usage"
RECORD: LiteralString = """
INSERT INTO llm_usage (model, purpose, input_tokens, output_tokens, cache_read, cache_write, usd, batch_id)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""


class UsageLedger:
    def __init__(self, conn: psycopg.Connection[Any], *, budget: Decimal = LLM_BUDGET_USD) -> None:
        self.conn = conn
        self.budget = budget

    def spent(self) -> Decimal:
        with self.conn.cursor() as cur:
            cur.execute(SPENT)
            row = cur.fetchone()
        # 읽자마자 닫는다: 판정하는 동안 열려 있으면 idle_in_transaction 15s 가 세션을 끊는다.
        self.conn.rollback()
        return Decimal(row[0]) if row else Decimal(0)

    def remaining(self) -> Decimal:
        return self.budget - self.spent()

    def check(self, model: str, usage: Usage, *, batch: bool = False) -> Decimal:
        """이 호출의 견적이 남은 예산에 들어가는지 — 들어가지 않으면 호출은 일어나지 않는다."""
        estimate = cost_usd(model, usage, batch=batch)
        left = self.remaining()
        if estimate > left:
            raise BudgetExceeded(
                f"{model}: this call is estimated at ${estimate:.4f} and only ${left:.4f} of the "
                f"${self.budget:.2f} budget is left (needs.llm_usage)"
            )
        return estimate

    def record(
        self,
        model: str,
        purpose: str,
        usage: Usage,
        *,
        batch: bool = False,
        batch_id: str | None = None,
    ) -> Decimal:
        usd = cost_usd(model, usage, batch=batch)
        with self.conn.cursor() as cur:
            cur.execute(
                RECORD,
                (
                    model,
                    purpose,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.cache_read,
                    usage.cache_write,
                    usd,
                    batch_id,
                ),
            )
        self.conn.commit()
        return usd


def budget_remaining(conn: psycopg.Connection[Any], budget: Decimal = LLM_BUDGET_USD) -> Decimal:
    return UsageLedger(conn, budget=budget).remaining()
