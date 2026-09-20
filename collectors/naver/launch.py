"""The `launch_onset` dataset (#285): two DataLab requests per product with a reviewed term list,
the monthly series they answer with, and the `not_after` claim those series make.

It is a module of its own rather than a third arm of `cli.py` because it is the one naver dataset
that writes evidence rather than source rows, and because its request shapes are not the `datalab`
dataset's: **one keyword group per search-trend request** and **one term per shopping-insight
keyword group**. A DataLab ratio is relative inside one request, so a second group would round a
small product's series toward zero beside a large one's -- and since the onset is read against the
term's *own* peak, nothing is compared across requests and no anchor group is needed at all, unlike
`datalab`, whose anchor exists precisely so two requests can be put on one scale (#90, #248).

**Why the collector writes the claims and not `analysis/`.** This side is the only one that knows
which terms were sent, which of the two APIs answered, and which products were refused for having
no line-specific term. `analysis/launch` reads the ledger and never fetches (#282's split), so a
projection living there would need the collector to hand it the same three facts through a second
table. What the collector owes in exchange is #282's DELETE duty, and it honours it scoped to this
one axis: `withdraw_launch_claims` per product, and `withdraw_vacated_launch_claims` once, at the
end of a pass that ran to completion.

Exit codes follow contracts/entrypoints.md, as the other two datasets do: 0 ok, 1 partial, 2
blocked. A product whose terms are refused, or whose series yields no onset, is neither -- it is a
counted outcome of an `ok` run, and the run's note carries the three counts the coverage question
asks for.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from collectors.naver import launch_terms, onset, parsing
from collectors.naver.models import LaunchClaim, LaunchSeriesPoint
from collectors.naver.scope import (
    DATALAB_TIME_UNIT,
    DATALAB_WINDOW_START,
    LAUNCH_SEARCH_GROUPS_PER_REQUEST,
    SHOPPING_CATEGORY,
    SHOPPING_MAX_KEYWORDS_PER_REQUEST,
    SHOPPING_WINDOW_START,
)
from collectors.naver.storage import db as storage_db
from collectors.naver.transport import AuthBlocked, BudgetExhausted, RateLimited, TransportError

if TYPE_CHECKING:
    from collectors.naver.cli import FetchSpec

#: The axis name every claim and every DELETE of this module is scoped to (#285; #283 owns the
#: other axes and they share the ledger).
AXIS = "datalab_onset"
#: contracts/versioning.md's `rule-vX.Y`. It versions the onset rule and its two volume guards, not
#: the term list: a re-reviewed term list is a new `source_ref`, which the DELETE duty handles.
ONSET_AXIS_VERSION = "rule-v1.0"


@dataclass(frozen=True, slots=True)
class ProductOutcome:
    """What one product came to, which is what the coverage line counts."""

    product_ref: str
    terms: tuple[str, ...]
    claimed_month: str | None
    apis: tuple[str, ...]  # which APIs yielded an onset
    refused: bool  # no line-specific term -- nothing was requested


def source_ref(terms: tuple[str, ...]) -> str:
    """The claim's `source_ref`: the term set, not a request.

    #282 offers "a DataLab `request_key`" as the example spelling, and that is wrong for a monthly
    pass: `request_key` hashes the body including `endDate`, which moves every run, so a re-read of
    the same product would mint a new live claim every month under a key the last one cannot
    overwrite. The thing this axis actually read off is the **term set**, and it is stable exactly
    as long as the claim is."""
    canonical = json.dumps(sorted(terms), ensure_ascii=False, separators=(",", ":"))
    return "terms:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def claim_for(outcome: ProductOutcome, *, match_strength: str, observed_at: datetime) -> LaunchClaim | None:
    """The `not_after` claim a product's onset makes, or None when it made none."""
    if not outcome.claimed_month:
        return None
    year, month = (int(part) for part in outcome.claimed_month.split("-"))
    return LaunchClaim(
        product_ref=outcome.product_ref,
        axis=AXIS,
        direction="not_after",
        # The month's first day. An upper bound at month precision is widened to the month's *last*
        # day by the reader (contracts/interfaces.md §Launch evidence), and an axis that widened it
        # here would put that rule in as many places as there are axes.
        claimed_on=date(year, month, 1),
        claimed_precision="month",
        source_ref=source_ref(outcome.terms),
        match_strength=match_strength,
        axis_version=ONSET_AXIS_VERSION,
        observed_at=observed_at,
        # Free text no rule reads: which of the two instruments answered, so a claim resting on one
        # of them can be told apart afterwards without re-running the pass.
        note="apis=" + "+".join(outcome.apis),
    )


def _terms_or_raise(terms: tuple[str, ...]) -> list[str]:
    kept = [t for t in terms if t.strip()]
    if not kept:
        raise ValueError("a launch-onset request needs at least one term")
    return kept


def search_spec(product_ref: str, terms: tuple[str, ...], *, end_date: date) -> FetchSpec:
    """One search-trend request: **one** keyword group holding every term of the product."""
    from collectors.naver.cli import FetchSpec as _Spec

    kept = _terms_or_raise(terms)
    return _Spec(
        kind="launch_trend",
        query=f"{product_ref} search_trend",
        params={
            # One group, and no anchor beside it (#90's anchor exists to put two requests on one
            # scale; nothing here is compared across requests).
            "keywordGroups": [{"groupName": product_ref, "keywords": kept}][
                :LAUNCH_SEARCH_GROUPS_PER_REQUEST
            ],
            "startDate": DATALAB_WINDOW_START,
            "endDate": end_date.isoformat(),
            "timeUnit": DATALAB_TIME_UNIT,
        },
    )


def shopping_spec(product_ref: str, terms: tuple[str, ...], *, end_date: date) -> FetchSpec:
    """One shopping-insight request: **one term per keyword group**, at most the vendor's five."""
    from collectors.naver.cli import FetchSpec as _Spec

    kept = _terms_or_raise(terms)[:SHOPPING_MAX_KEYWORDS_PER_REQUEST]
    return _Spec(
        kind="launch_shopping",
        query=f"{product_ref} shopping_insight",
        params={
            "startDate": SHOPPING_WINDOW_START,
            "endDate": end_date.isoformat(),
            "timeUnit": DATALAB_TIME_UNIT,
            "category": SHOPPING_CATEGORY,
            # `k0`..`k4` rather than the terms: this is the body shape the vendor was measured
            # accepting on 2026-09-20, and the series come back in the order they were sent, which
            # is how `parsing.parse_launch_shopping_response` maps each one to its term.
            "keyword": [{"name": f"k{index}", "param": [term]} for index, term in enumerate(kept)],
        },
    )


def _ask(
    fetcher: Any, journal: Any, spec: FetchSpec, *, product_ref: str, terms: tuple[str, ...], now: datetime
) -> tuple[list[LaunchSeriesPoint], bool, TransportError | None]:
    """One request. Returns its points, whether it failed, and -- when there is one -- the error
    that ends the whole run."""
    try:
        body = fetcher.fetch(spec)
    except (AuthBlocked, RateLimited, BudgetExhausted) as error:
        # All three say the same thing about every request still to come.
        journal.record(query=spec.query, status=error.status, attempt=1, error=str(error))
        return [], False, error
    except Exception as error:  # noqa: BLE001 - one product's failure must not stop the pass
        journal.record(query=spec.query, status=getattr(error, "status", None), attempt=1, error=str(error))
        return [], True, None
    journal.record(query=spec.query, status=200, attempt=1)
    request_key = parsing.datalab_request_key(spec.params)
    if spec.kind == "launch_trend":
        points = parsing.parse_launch_trend_response(
            body, product_ref=product_ref, terms=terms, captured_at=now, request_key=request_key
        )
    else:
        points = parsing.parse_launch_shopping_response(
            body,
            product_ref=product_ref,
            terms=tuple(_terms_or_raise(terms)[:SHOPPING_MAX_KEYWORDS_PER_REQUEST]),
            captured_at=now,
            request_key=request_key,
        )
    return points, False, None


def _onset_of(points: list[LaunchSeriesPoint]) -> str | None:
    by_series: dict[str, list[tuple[str, float | None]]] = {}
    for point in points:
        by_series.setdefault(point.series_key, []).append((point.month, point.ratio))
    return onset.api_onset(by_series)


def run(engine: Any, fetcher: Any, journal: Any, *, now: datetime, terms_path: Path | None = None) -> Any:
    """One `launch_onset` pass over every product the term list names."""
    from collectors.naver.cli import _Outcome

    wanted = launch_terms.load(terms_path or launch_terms.TERMS_PATH)
    if not wanted:
        # An empty file means "not configured yet", never "withdraw everything this axis claimed" --
        # and the file ships empty, so this is the state a first deploy is in.
        return _Outcome("blocked", 2, "launch_terms.json names no product")

    with engine.begin() as connection:
        catalogue = storage_db.read_product_refs(connection)
    by_ref = {ref.product_ref: ref for ref in catalogue}

    end_date = now.date()
    considered: list[str] = []
    claimed = 0
    no_onset: list[str] = []
    failed: list[str] = []
    stopped: TransportError | None = None
    for product_ref, terms in wanted.items():
        ref = by_ref.get(product_ref)
        if ref is None:
            # The catalogue no longer holds this ref -- the foreign key would refuse the claim, and
            # the reviewed list is what has to be corrected. Counted, not guessed at.
            failed.append(product_ref)
            continue
        points: list[LaunchSeriesPoint] = []
        apis: list[str] = []
        hurt = False
        for spec in (
            search_spec(product_ref, terms, end_date=end_date),
            shopping_spec(product_ref, terms, end_date=end_date),
        ):
            got, one_failed, stopped = _ask(
                fetcher, journal, spec, product_ref=product_ref, terms=terms, now=now
            )
            hurt = hurt or one_failed
            points += got
            if stopped is not None:
                break
        if stopped is not None:
            break
        if hurt:
            failed.append(product_ref)
        considered.append(product_ref)

        months = []
        for api in ("search_trend", "shopping_insight"):
            found = _onset_of([p for p in points if p.api == api])
            if found:
                apis.append(api)
                months.append(found)
        outcome = ProductOutcome(
            product_ref=product_ref,
            terms=terms,
            claimed_month=onset.combine(months),
            apis=tuple(apis),
            refused=False,
        )
        claim = claim_for(
            outcome,
            match_strength=launch_terms.match_strength(terms, ref, catalogue),
            observed_at=now,
        )
        with engine.begin() as connection:
            storage_db.write_launch_series(connection, points)
            if claim is None:
                no_onset.append(product_ref)
            else:
                storage_db.write_launch_claims(connection, [claim])
                claimed += 1
            # Scoped to this axis and this product: the old term set's claim is not a second
            # opinion, it is a statement this axis no longer makes (#282's DELETE duty).
            storage_db.withdraw_launch_claims(
                connection,
                axis=AXIS,
                product_ref=product_ref,
                keep_source_ref=None if claim is None else claim.source_ref,
            )

    if stopped is not None:
        # The pass did not run to the end, so nothing is withdrawn: a rate limit is not evidence
        # that a product stopped having an onset. Partial means we yielded, blocked means we were
        # refused -- the same split the other two datasets keep.
        print(f"launch_onset: {claimed} claim(s) before the run stopped")
        if claimed == 0:
            return _Outcome("blocked", 2, f"launch_onset stopped: {stopped}")
        return _Outcome("partial", 1, f"launch_onset stopped: {stopped}")

    with engine.begin() as connection:
        # The other half of the duty, and only here: a `product_ref` wobbles when the catalogue is
        # re-clustered, so a claim of this axis on a ref this complete pass never considered is one
        # nothing else will ever withdraw.
        vacated = storage_db.withdraw_vacated_launch_claims(connection, axis=AXIS, considered=considered)

    coverage = (
        f"launch_onset: {claimed} claim(s), {len(no_onset)} with no onset, "
        f"{len(wanted)} product(s) in the term list, {len(failed)} failed, {vacated} withdrawn"
    )
    print(coverage)
    if failed and claimed == 0:
        return _Outcome("blocked", 2, f"every product failed: {', '.join(failed)}")
    if failed:
        return _Outcome("partial", 1, f"{len(failed)} product(s) failed: {', '.join(failed)}")
    return _Outcome("ok", 0, coverage)


__all__ = [
    "AXIS",
    "ONSET_AXIS_VERSION",
    "ProductOutcome",
    "source_ref",
    "claim_for",
    "search_spec",
    "shopping_spec",
    "run",
]
