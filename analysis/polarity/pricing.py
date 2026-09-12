"""Per-model rates and the hard stop. The amount itself is the COSMAI_LLM_BUDGET_USD knob (#136), the running
total is needs.llm_usage (DDL 003), and the block is *before* the call.

Counting after the call does not bring back money that has already gone out. So reserve() locks, reads,
writes an estimate row and commits inside one transaction: even when no response follows (timeout, Ctrl-C,
exception) the reservation stays in the ledger and comes off the next run's budget. settle() overwrites that
row with the measurement — adding a new row would count it twice.

Without the lock two runs read the same remaining() and both submit. pg_advisory_xact_lock puts the read and
the reservation into a single line.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, LiteralString

import psycopg

# Source: claude-api skill §Current Models (cached 2026-06-24) as read on 2026-08-24. $ per 1M tokens.
# cache read = input x 0.1, cache write (5-minute ephemeral) = input x 1.25 (same skill §Prompt Caching).
# Sonnet 5 is at the intro $2/$10 until 2026-08-31, but the hard stop counts list price — better early
# than late.
PRICES_SOURCE_DATE = "2026-08-24"
PER_MILLION = Decimal(1_000_000)
BATCH_DISCOUNT = Decimal("0.5")  # The Batches API is 50% on every token
OLLAMA_PREFIX = "ollama:"

# The two knobs of #136. Neither is a secret, so their values live in stack/env.example -> stack/.env and
# compose hands them to the analyze container (contracts/secrets.md, "what is not a secret").
BUDGET_KEY = "COSMAI_LLM_BUDGET_USD"
CHAIN_KEY = "COSMAI_LLM_CHAIN"
CHAIN_SEPARATOR = ","


class KnobMissing(LookupError):
    """A knob this module needs is absent or unreadable.

    A `LookupError` and not `SystemExit`: every entry point already has one handler for "refused before
    anything started", and all four are written around `LookupError` -- `cosmai/cli.py`'s analyze, eval and
    retrieval paths, and `ask.BLOCKING`. A `BaseException` walks past all of them and leaves exit 1, which
    `contracts/entrypoints.md` gives to a run that failed and, on `ask`, to a query with no evidence: a host
    that was never configured would read as a corpus with nothing to say. `analysis/retrieval/ask.client_for`
    converts `db/secrets.py`'s `SystemExit` for exactly this reason; this raises the right class outright."""


def budget_usd() -> Decimal:
    """The hard stop in USD, read from the environment every time it is needed.

    There is deliberately no default. An amount that lived in this file needed a commit and a redeploy to
    change and STATE.md carried a second copy of it that could disagree (#136); an amount defaulted *here*
    would put both failures back one layer down, and a deployment that forgot the knob would spend against a
    number nobody chose. Forgetting has to be loud, so an absent or unreadable knob refuses the command
    (exit 2) before it starts."""
    raw = os.environ.get(BUDGET_KEY, "").strip()
    if not raw:
        raise KnobMissing(f"{BUDGET_KEY} is not set; it carries the LLM hard stop in USD (stack/.env)")
    try:
        amount = Decimal(raw)
    except InvalidOperation:
        raise KnobMissing(f"{BUDGET_KEY}={raw!r} is not a number of USD") from None
    # is_finite() first: NaN is a Decimal that compares to nothing, and `< 0` on it signals rather than
    # answers. Infinity is rejected for what it means here -- an unlimited budget, spelled out.
    if not amount.is_finite() or amount < 0:
        raise KnobMissing(
            f"{BUDGET_KEY}={raw!r} is not a hard stop; it must be a finite amount, zero or more"
        )
    return amount


def llm_chain() -> tuple[str, ...]:
    """The polarity implementations to try, in the order they are tried -- the fallback order #113 walks.

    Same rule as the budget and for the same reason: absent means refuse, never "pick something". Only the
    shape is checked here (`<name>:<argument>`, the spec grammar `--impl` already uses); whether a spec names
    a registered factory is the caller's question, since the registry is loaded later than this."""
    raw = os.environ.get(CHAIN_KEY, "").strip()
    if not raw:
        raise KnobMissing(
            f"{CHAIN_KEY} is not set; it names the polarity implementations in fallback order (stack/.env)"
        )
    steps = tuple(step.strip() for step in raw.split(CHAIN_SEPARATOR))
    malformed = [step for step in steps if len(parts := step.split(":", 1)) != 2 or not all(parts)]
    if malformed:
        raise KnobMissing(f"{CHAIN_KEY}={raw!r} is not a chain of <impl>:<argument> specs: {malformed}")
    return steps


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


@dataclass(frozen=True)
class PurposeCap:
    """A ceiling one purpose carries on top of the shared budget. Either half may be left off."""

    per_call: Decimal | None = None
    per_day: Decimal | None = None


class BudgetExceeded(RuntimeError):
    """The hard stop. At the moment this exception is raised the call has not gone out yet."""


def price_for(model: str) -> Price:
    if model.startswith(OLLAMA_PREFIX):
        return FREE  # A local model is free — it goes in the ledger but eats no budget
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


# The issue number is the lock key — one run at a time passes through reading the budget and reserving.
ADVISORY_KEY = 6
LOCK: LiteralString = "SELECT pg_advisory_xact_lock(%s)"
SPENT: LiteralString = "SELECT coalesce(sum(usd), 0) FROM llm_usage"
# Both states of one purpose: settle() overwrites the reservation row, so a call is `reserve:<p>`
# until it lands and `<p>` after -- one row either way, never both. The boundary is the UTC day
# whatever the session's TimeZone is set to.
SPENT_TODAY_BY_PURPOSE: LiteralString = (
    "SELECT coalesce(sum(usd), 0) FROM llm_usage WHERE purpose IN (%s, %s) "
    "AND called_at >= date_trunc('day', now() AT TIME ZONE 'utc') AT TIME ZONE 'utc'"
)
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
    """One estimate row held in the ledger before submission. settle() overwrites it once the measurement
    arrives."""

    id: int
    model: str
    usd: Decimal
    batch: bool


class UsageLedger:
    def __init__(
        self,
        conn: psycopg.Connection[Any],
        *,
        budget: Decimal | None = None,
        caps: Mapping[str, PurposeCap] | None = None,
    ) -> None:
        self.conn = conn
        # Resolved here rather than at import: a caller that names no budget gets the knob's value, and a
        # deployment that set no knob dies building the ledger -- before the first call, never after it.
        self.budget = budget_usd() if budget is None else budget
        self.caps = dict(caps or {})

    def spent(self) -> Decimal:
        with self.conn.cursor() as cur:
            cur.execute(SPENT)
            row = cur.fetchone()
        # Closed as soon as it is read: held open during a decision, idle_in_transaction 15s cuts the session.
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
        """Lock → read the total → check the estimate → reservation row → commit, all one transaction. The
        call goes out after this."""
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
            cap = self.caps.get(purpose)
            if cap is not None:
                self._check_cap(cur, cap, model, purpose, usd)
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

    def _check_cap(self, cur: Any, cap: PurposeCap, model: str, purpose: str, usd: Decimal) -> None:
        """The per-purpose ceilings, read inside reserve's lock and before its row -- a refusal must
        leave the ledger exactly as it found it."""
        if cap.per_call is not None and usd > cap.per_call:
            self.conn.rollback()
            raise BudgetExceeded(
                f"{model}: this call is estimated at ${usd:.4f}, over the ${cap.per_call:.2f} "
                f"per-call cap for {purpose}"
            )
        if cap.per_day is not None:
            cur.execute(SPENT_TODAY_BY_PURPOSE, (purpose, f"reserve:{purpose}"))
            row = cur.fetchone()
            today = Decimal(row[0]) if row else Decimal(0)
            if today + usd > cap.per_day:
                self.conn.rollback()
                raise BudgetExceeded(
                    f"{model}: this call (${usd:.4f}) would take {purpose} to ${today + usd:.4f} "
                    f"today, over the ${cap.per_day:.2f} per-day cap (UTC day)"
                )

    def settle(
        self,
        reservation: Reservation,
        purpose: str,
        usage: Usage,
        *,
        batch_id: str | None = None,
    ) -> Decimal:
        """Overwrites the reservation row with the measurement. Adding a row would leave the reservation in
        place and take the budget twice."""
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
        """Finds the reservation row again by batch_id — settlement attaches even when submit and collect are
        different runs."""
        with self.conn.cursor() as cur:
            cur.execute(RESERVED, (batch_id,))
            row = cur.fetchone()
        self.conn.rollback()
        if row is None:
            return None
        return Reservation(id=int(row[0]), model=str(row[1]), usd=Decimal(row[2]), batch=True)

    def attach_batch_id(self, reservation: Reservation, batch_id: str) -> None:
        """Attaches the collection address to the reservation row right after submission — it is the only key
        that gets the results back for 29 days."""
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
        """Writes down spending that already happened (a free local model, a test's advance credit). No budget
        check."""
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


def budget_remaining(conn: psycopg.Connection[Any], budget: Decimal | None = None) -> Decimal:
    return UsageLedger(conn, budget=budget).remaining()
