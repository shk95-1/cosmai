"""모델별 단가와 $7.00 하드스톱. 누적은 needs.llm_usage(DDL 003)이고 차단은 호출 *전*이다.

호출 후에 세면 이미 나간 돈은 돌아오지 않는다. 그래서 reserve() 가 한 트랜잭션 안에서 잠그고·읽고·
견적 행을 쓰고 커밋한다: 그 뒤 응답이 오지 않아도(타임아웃·Ctrl-C·예외) 예약분은 원장에 남아 다음
실행의 예산에서 빠진다. settle() 이 그 행을 실측으로 덮어쓴다 — 새 행을 더하면 이중 계상이 된다.

잠금이 없으면 두 실행이 같은 remaining() 을 읽고 둘 다 제출한다. pg_advisory_xact_lock 이 읽기와
예약 사이를 한 줄로 만든다.
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


# 이슈 번호를 잠금 키로 쓴다 — 예산을 읽고 예약하는 구간을 지나는 실행은 한 번에 하나다.
ADVISORY_KEY = 6
LOCK: LiteralString = "SELECT pg_advisory_xact_lock(%s)"
SPENT: LiteralString = "SELECT coalesce(sum(usd), 0) FROM llm_usage"
RECORD: LiteralString = """
INSERT INTO llm_usage (model, purpose, input_tokens, output_tokens, cache_read, cache_write, usd, batch_id)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
"""
SETTLE: LiteralString = """
UPDATE llm_usage SET called_at = now(), purpose = %s, input_tokens = %s, output_tokens = %s,
       cache_read = %s, cache_write = %s, usd = %s, batch_id = coalesce(%s, batch_id)
WHERE id = %s
"""
RESERVED: LiteralString = "SELECT id, model, usd FROM llm_usage WHERE batch_id = %s ORDER BY id LIMIT 1"


@dataclass(frozen=True)
class Reservation:
    """제출 전에 원장에 잡아 둔 견적 한 행. 실측이 오면 settle() 이 이 행을 덮어쓴다."""

    id: int
    model: str
    usd: Decimal
    batch: bool


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

    def reserve(
        self,
        model: str,
        purpose: str,
        usage: Usage,
        *,
        batch: bool = False,
        batch_id: str | None = None,
    ) -> Reservation:
        """잠금 → 누적 읽기 → 견적 검사 → 예약 행 → 커밋, 전부 한 트랜잭션. 호출은 이 뒤에 나간다."""
        usd = cost_usd(model, usage, batch=batch)
        with self.conn.cursor() as cur:
            cur.execute(LOCK, (ADVISORY_KEY,))
            cur.execute(SPENT)
            row = cur.fetchone()
            left = self.budget - (Decimal(row[0]) if row else Decimal(0))
            if usd > left:
                self.conn.rollback()
                raise BudgetExceeded(
                    f"{model}: this call is estimated at ${usd:.4f} and only ${left:.4f} of the "
                    f"${self.budget:.2f} budget is left (needs.llm_usage)"
                )
            cur.execute(
                RECORD,
                (
                    model,
                    f"reserve:{purpose}",
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.cache_read,
                    usage.cache_write,
                    usd,
                    batch_id,
                ),
            )
            reserved = cur.fetchone()
        self.conn.commit()
        return Reservation(id=int(reserved[0]) if reserved else 0, model=model, usd=usd, batch=batch)

    def settle(
        self,
        reservation: Reservation,
        purpose: str,
        usage: Usage,
        *,
        batch_id: str | None = None,
    ) -> Decimal:
        """예약 행을 실측으로 덮어쓴다. 행을 더하면 예약분이 남아 예산이 두 번 깎인다."""
        usd = cost_usd(reservation.model, usage, batch=reservation.batch)
        with self.conn.cursor() as cur:
            cur.execute(
                SETTLE,
                (
                    purpose,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.cache_read,
                    usage.cache_write,
                    usd,
                    batch_id,
                    reservation.id,
                ),
            )
        self.conn.commit()
        return usd

    def reservation_for(self, batch_id: str) -> Reservation | None:
        """batch_id 로 예약 행을 되찾는다 — submit 과 collect 가 다른 실행이어도 정산이 붙는다."""
        with self.conn.cursor() as cur:
            cur.execute(RESERVED, (batch_id,))
            row = cur.fetchone()
        self.conn.rollback()
        if row is None:
            return None
        return Reservation(id=int(row[0]), model=str(row[1]), usd=Decimal(row[2]), batch=True)

    def attach_batch_id(self, reservation: Reservation, batch_id: str) -> None:
        """제출 직후 회수 주소를 예약 행에 붙인다 — 그것이 29일간 결과를 되찾을 유일한 키다."""
        with self.conn.cursor() as cur:
            cur.execute("UPDATE llm_usage SET batch_id = %s WHERE id = %s", (batch_id, reservation.id))
        self.conn.commit()

    def record(
        self,
        model: str,
        purpose: str,
        usage: Usage,
        *,
        batch: bool = False,
        batch_id: str | None = None,
    ) -> Decimal:
        """이미 일어난 지출을 그대로 적는다 (무료인 로컬 모델·테스트의 사전 적립). 예산 검사는 없다."""
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
