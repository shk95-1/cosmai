"""The `launch_onset` pass end to end against a real Postgres, with a fake fetcher (#285).

The fake answers with a series it builds from a **shape**, not from the request, so it can produce
the four things a live run meets and a request-shaped fake never would (#266, "fake fetchers
fabricate success"): a **flat** series, a series whose onset is in the **month in progress**, an API
with **no series at all**, and a **4xx**. Each of those is a case where the axis has to write *less*
than it could, and writing less is exactly what a fake cannot invent for itself.

The two APIs answer over different windows -- search trend from 2016-01, shopping insight from
2017-08 -- so a shape is given as the month it starts rising in and each series is built back from
its own window's first month.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa

from collectors.naver import cli, launch, scope
from collectors.naver.cli import FetchSpec, run
from collectors.naver.storage import db as storage_db
from collectors.naver.storage.tables import (
    naver_fetch_log,
    naver_launch_series,
    naver_run,
    product_launch_evidence,
)
from collectors.naver.transport import HttpFetcher, RateLimited, RequestFailed

pytestmark = pytest.mark.postgres

AT = datetime(2026, 9, 20, 7, 20, tzinfo=UTC)
FIXTURES = Path(__file__).resolve().parent / "fixtures"
TERMS = FIXTURES / "launch_terms.json"
CATALOGUE = FIXTURES / "launch_catalogue.json"

SEARCH_START = (2016, 1)
SHOPPING_START = (2017, 8)
THIS_MONTH = (2026, 9)

FLAT = ("flat",)


def _span(start: tuple[int, int], end: tuple[int, int]) -> int:
    return (end[0] - start[0]) * 12 + (end[1] - start[1])


def _periods(count: int, start: tuple[int, int]) -> list[str]:
    year, month = start
    out = []
    for _ in range(count):
        out.append(f"{year:04d}-{month:02d}-01")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def _ratios(shape: tuple[Any, ...], start: tuple[int, int]) -> list[float]:
    if shape == FLAT:
        # Above a tenth of its own peak from the window's first month: no onset at all.
        return [50.0] * (_span(start, THIS_MONTH) + 1)
    onset = shape[1]
    quiet = [0.0] * _span(start, onset)
    if onset == THIS_MONTH:
        return [*quiet, 100.0]  # the month in progress, and nothing after it yet
    return [*quiet, 30.0, 60.0, 100.0, 70.0]


def _data(shape: tuple[Any, ...], start: tuple[int, int]) -> list[dict[str, Any]]:
    ratios = _ratios(shape, start)
    return [
        {"period": period, "ratio": ratio}
        for period, ratio in zip(_periods(len(ratios), start), ratios, strict=True)
    ]


def rising(year: int, month: int) -> tuple[Any, ...]:
    return ("rising", (year, month))


class _FakeFetcher:
    """`series` maps a product_ref to `{api: shape}`. An api missing from a product's entry answers
    with no series at all; a product named in `fails` gets a 4xx on every request."""

    def __init__(
        self,
        series: dict[str, dict[str, tuple[Any, ...]]],
        *,
        fails: Collection[str] = (),
        per_term: dict[str, dict[str, tuple[Any, ...]]] | None = None,
    ) -> None:
        self.calls: list[FetchSpec] = []
        self._series = series
        self._fails = frozenset(fails)
        self._per_term = per_term or {}

    def fetch(self, spec: FetchSpec) -> dict[str, Any]:
        self.calls.append(spec)
        product_ref = spec.query.split(" ", 1)[0]
        if product_ref in self._fails:
            raise RequestFailed("HTTP 400 (errorCode=SE01)", status=400, error_code="SE01")
        held = self._series.get(product_ref, {})
        if spec.kind == "launch_trend":
            shape = held.get("search_trend")
            if shape is None:
                return {"results": []}
            group = spec.params["keywordGroups"][0]
            return {
                "results": [
                    {
                        "title": group["groupName"],
                        "keywords": list(group["keywords"]),
                        "data": _data(shape, SEARCH_START),
                    }
                ]
            }
        shape = held.get("shopping_insight")
        if shape is None:
            return {"results": []}
        by_term = self._per_term.get(product_ref, {})
        # One series per keyword group, in the order the request sent them -- the vendor's shape.
        return {
            "results": [
                {
                    "title": group["name"],
                    "data": _data(by_term.get(group["param"][0], shape), SHOPPING_START),
                }
                for group in spec.params["keyword"]
            ]
        }


@pytest.fixture
def secret_file(tmp_path: Path) -> Path:
    path = tmp_path / "env"
    path.write_text(
        "COSMA_SRC_NAVER_BLOG_CLIENT_ID=id\nCOSMA_SRC_NAVER_BLOG_CLIENT_SECRET=secret\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def catalogue(needs_runtime_url: str) -> str:
    """The `needs.product_ref` rows the claims' foreign key needs."""
    raw = json.loads(CATALOGUE.read_text(encoding="utf-8"))
    engine = sa.create_engine(needs_runtime_url)
    with engine.begin() as conn:
        for ref in raw["refs"]:
            conn.execute(
                sa.text(
                    "INSERT INTO product_ref (product_ref, brand, name_norm, name, linker_version)"
                    " VALUES (:r, :b, :n, :n, 'rule-v1.0')"
                ),
                {"r": ref["product_ref"], "b": ref["brand"], "n": ref["name_norm"]},
            )
    engine.dispose()
    return needs_runtime_url


def _claims(url: str) -> Sequence[Any]:
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        rows = conn.execute(
            sa.select(product_launch_evidence).order_by(
                product_launch_evidence.c.product_ref, product_launch_evidence.c.axis
            )
        ).all()
    engine.dispose()
    return rows


def _go(url: str, secret_file: Path, fetcher: Any, *, at: datetime = AT, terms: Path = TERMS) -> int:
    return run(
        "launch_onset",
        database_url=url,
        secrets_path=secret_file,
        fetcher=fetcher,
        captured_at=at,
        terms_path=terms,
    )


def test_the_pass_writes_one_not_after_claim_per_product_with_an_onset(catalogue, secret_file):
    fetcher = _FakeFetcher(
        {
            ref: {"search_trend": rising(2023, 5), "shopping_insight": rising(2023, 5)}
            for ref in ("oy:A1", "oy:B1", "oy:C1")
        }
    )
    assert _go(catalogue, secret_file, fetcher) == 0

    rows = _claims(catalogue)
    assert [r.product_ref for r in rows] == ["oy:A1", "oy:B1", "oy:C1"]
    assert {r.axis for r in rows} == {launch.AXIS}
    assert {r.direction for r in rows} == {"not_after"}
    assert {r.claimed_precision for r in rows} == {"month"}
    assert {r.axis_version for r in rows} == {launch.ONSET_AXIS_VERSION}
    # oy:B1's line name is contained in oy:B2's, so its join is `partial`; the other two are exact.
    assert {r.product_ref: r.match_strength for r in rows} == {
        "oy:A1": "exact",
        "oy:B1": "partial",
        "oy:C1": "exact",
    }


def test_the_claimed_month_is_the_later_of_the_two_apis(catalogue, secret_file):
    fetcher = _FakeFetcher({"oy:A1": {"search_trend": rising(2023, 5), "shopping_insight": rising(2023, 6)}})
    _go(catalogue, secret_file, fetcher)
    rows = _claims(catalogue)
    assert len(rows) == 1
    # The conservative bound is the later of the two, never the earlier.
    assert rows[0].claimed_on.isoformat() == "2023-06-01"


def test_inside_one_api_the_earliest_term_decides(catalogue, secret_file):
    # The terms of one product are the same observation spelled differently, so the first month any
    # of them took off is what that instrument saw. (Across the two APIs it is the other way: two
    # instruments, and the axis keeps the weaker bound.)
    terms = json.loads(TERMS.read_text(encoding="utf-8"))["products"]["oy:A1"]
    fetcher = _FakeFetcher(
        {"oy:A1": {"search_trend": rising(2024, 1), "shopping_insight": rising(2023, 9)}},
        per_term={"oy:A1": {terms[0]: rising(2023, 4), terms[1]: rising(2023, 9)}},
    )
    _go(catalogue, secret_file, fetcher)
    rows = _claims(catalogue)
    # shopping saw 2023-04 first; search saw 2024-01; the later of the two instruments wins.
    assert rows[0].claimed_on.isoformat() == "2024-01-01"


def test_one_api_with_no_series_at_all_still_leaves_the_other_ones_claim(catalogue, secret_file):
    fetcher = _FakeFetcher({"oy:A1": {"search_trend": rising(2023, 5)}})
    assert _go(catalogue, secret_file, fetcher) == 0
    rows = _claims(catalogue)
    assert len(rows) == 1
    assert rows[0].claimed_on.isoformat() == "2023-05-01"
    assert "search_trend" in (rows[0].note or "")
    assert "shopping_insight" not in (rows[0].note or "")


def test_a_flat_series_produces_no_claim_at_all(catalogue, secret_file):
    fetcher = _FakeFetcher({"oy:A1": {"search_trend": FLAT, "shopping_insight": FLAT}})
    _go(catalogue, secret_file, fetcher)
    assert _claims(catalogue) == []
    # The series is still stored: it is the evidence that there is no onset, and a later rule
    # version reads it without asking NAVER again.
    engine = sa.create_engine(catalogue)
    with engine.begin() as conn:
        stored = conn.execute(sa.select(sa.func.count()).select_from(naver_launch_series)).scalar_one()
    engine.dispose()
    assert stored > 0


def test_an_onset_in_the_month_in_progress_is_claimed(catalogue, secret_file):
    fetcher = _FakeFetcher({"oy:A1": {"search_trend": ("rising", THIS_MONTH)}})
    _go(catalogue, secret_file, fetcher)
    rows = _claims(catalogue)
    assert len(rows) == 1
    assert rows[0].claimed_on.isoformat() == "2026-09-01"


def test_a_failed_request_costs_that_product_and_leaves_the_run_partial(catalogue, secret_file):
    fetcher = _FakeFetcher(
        {ref: {"search_trend": rising(2023, 5)} for ref in ("oy:A1", "oy:C1")},
        fails=["oy:B1"],
    )
    assert _go(catalogue, secret_file, fetcher) == 1
    assert [r.product_ref for r in _claims(catalogue)] == ["oy:A1", "oy:C1"]

    engine = sa.create_engine(catalogue)
    with engine.begin() as conn:
        status = conn.execute(sa.select(naver_run.c.status, naver_run.c.dataset)).all()
        errors = conn.execute(
            sa.select(sa.func.count()).select_from(naver_fetch_log).where(naver_fetch_log.c.status == 400)
        ).scalar_one()
    engine.dispose()
    assert status == [("partial", "launch_onset")]
    assert errors >= 1


def test_a_failed_request_never_withdraws_the_claim_that_was_already_there(catalogue, secret_file):
    # A 4xx is something NAVER said about one call, not evidence that a product stopped having an
    # onset. Withdrawing on it would flip the product's verdict on the month the vendor happened to
    # be unhappy, and the next month's pass would put the same claim back -- with nothing anywhere
    # saying the metric had moved.
    _go(catalogue, secret_file, _FakeFetcher({"oy:A1": {"search_trend": rising(2023, 5)}}))
    (standing,) = _claims(catalogue)
    assert standing.product_ref == "oy:A1"

    assert _go(catalogue, secret_file, _FakeFetcher({}, fails=["oy:A1"])) == 1
    rows = _claims(catalogue)
    assert [r.product_ref for r in rows] == ["oy:A1"]
    assert rows[0].claimed_on == standing.claimed_on


def test_two_apis_that_answer_with_no_series_do_withdraw(catalogue, secret_file):
    # The other side of the rule above: nothing failed, both instruments answered, and what they
    # answered is that these terms have no series. That is evidence, and the claim goes.
    _go(catalogue, secret_file, _FakeFetcher({"oy:A1": {"search_trend": rising(2023, 5)}}))
    assert len(_claims(catalogue)) == 1
    assert _go(catalogue, secret_file, _FakeFetcher({})) == 0
    assert _claims(catalogue) == []


def test_a_refused_product_is_never_requested(catalogue, secret_file):
    # oy:D1's only terms would be its brand and the category word, so it is not in the term file
    # and no request is spent on it.
    fetcher = _FakeFetcher({"oy:A1": {"search_trend": rising(2023, 5)}})
    _go(catalogue, secret_file, fetcher)
    assert all("oy:D1" not in spec.query for spec in fetcher.calls)


def test_an_empty_term_file_blocks_the_run_and_withdraws_nothing(catalogue, secret_file, tmp_path: Path):
    _go(catalogue, secret_file, _FakeFetcher({"oy:A1": {"search_trend": rising(2023, 5)}}))
    assert len(_claims(catalogue)) == 1

    empty = tmp_path / "launch_terms.json"
    empty.write_text(json.dumps({"generic": [], "products": {}}), encoding="utf-8")
    # The shipped file is empty, and an empty file means "not configured yet", never "withdraw
    # everything this axis ever claimed".
    assert _go(catalogue, secret_file, _FakeFetcher({}), terms=empty) == 2
    assert len(_claims(catalogue)) == 1


def test_a_search_request_holds_one_group_and_a_shopping_group_holds_one_term(catalogue, secret_file):
    fetcher = _FakeFetcher({"oy:A1": {"search_trend": rising(2023, 5), "shopping_insight": FLAT}})
    _go(catalogue, secret_file, fetcher)
    searches = [s for s in fetcher.calls if s.kind == "launch_trend"]
    shoppings = [s for s in fetcher.calls if s.kind == "launch_shopping"]
    assert searches and shoppings
    assert all(len(s.params["keywordGroups"]) == 1 for s in searches)
    assert all(all(len(g["param"]) == 1 for g in s.params["keyword"]) for s in shoppings)
    assert all(scope.DATALAB_ANCHOR not in json.dumps(s.params, ensure_ascii=False) for s in searches)


def test_two_requests_a_product_and_no_more(catalogue, secret_file):
    fetcher = _FakeFetcher({ref: {"search_trend": rising(2023, 5)} for ref in ("oy:A1", "oy:B1", "oy:C1")})
    _go(catalogue, secret_file, fetcher)
    assert len(fetcher.calls) == 3 * scope.LAUNCH_REQUESTS_PER_PRODUCT


def test_a_rerun_restates_the_claim_in_place(catalogue, secret_file):
    fetcher = _FakeFetcher({"oy:A1": {"search_trend": rising(2023, 5)}})
    _go(catalogue, secret_file, fetcher)
    _go(catalogue, secret_file, fetcher, at=datetime(2026, 10, 1, 7, 20, tzinfo=UTC))
    # The window moved, so every request key moved with it -- and the claim is still one row,
    # because its source_ref is the term set and not the request.
    assert len(_claims(catalogue)) == 1


def test_a_product_that_loses_its_onset_has_its_claim_withdrawn(catalogue, secret_file):
    _go(catalogue, secret_file, _FakeFetcher({"oy:A1": {"search_trend": rising(2023, 5)}}))
    assert len(_claims(catalogue)) == 1
    _go(catalogue, secret_file, _FakeFetcher({"oy:A1": {"search_trend": FLAT}}))
    assert _claims(catalogue) == []


def test_the_withdrawal_never_reaches_another_axis(catalogue, secret_file):
    # #283's axes share this ledger. A DELETE that is not scoped to `datalab_onset` would take
    # their evidence with it, and nothing else in the system would notice.
    engine = sa.create_engine(catalogue)
    with engine.begin() as conn:
        conn.execute(
            sa.insert(product_launch_evidence).values(
                product_ref="oy:A1",
                axis="mfds_report",
                direction="not_before",
                claimed_on="2023-01-15",
                claimed_precision="day",
                source_ref="report_seq:1",
                match_strength="exact",
                axis_version="rule-v1.0",
                observed_at=AT,
            )
        )
    engine.dispose()

    _go(catalogue, secret_file, _FakeFetcher({"oy:A1": {"search_trend": FLAT}}))
    assert [(r.product_ref, r.axis) for r in _claims(catalogue)] == [("oy:A1", "mfds_report")]


def test_a_claim_on_a_ref_the_pass_no_longer_considers_is_withdrawn(catalogue, secret_file):
    # The `product_ref` a product is known by wobbles when the catalogue is re-clustered (#282), so
    # a claim can stay live on a ref that no longer names anything the axis asks about.
    engine = sa.create_engine(catalogue)
    with engine.begin() as conn:
        conn.execute(
            sa.insert(product_launch_evidence).values(
                product_ref="oy:D1",
                axis=launch.AXIS,
                direction="not_after",
                claimed_on="2019-03-01",
                claimed_precision="month",
                source_ref="terms:deadbeef",
                match_strength="partial",
                axis_version=launch.ONSET_AXIS_VERSION,
                observed_at=AT,
            )
        )
    engine.dispose()

    _go(catalogue, secret_file, _FakeFetcher({"oy:A1": {"search_trend": rising(2023, 5)}}))
    assert [r.product_ref for r in _claims(catalogue)] == ["oy:A1"]


def test_a_run_that_stopped_early_withdraws_nothing_it_did_not_reach(catalogue, secret_file):
    _go(
        catalogue,
        secret_file,
        _FakeFetcher({ref: {"search_trend": rising(2023, 5)} for ref in ("oy:A1", "oy:B1", "oy:C1")}),
    )
    assert len(_claims(catalogue)) == 3

    class _RateLimited:
        def fetch(self, spec: FetchSpec) -> dict[str, Any]:
            raise RateLimited("HTTP 429 (errorCode=429)", status=429, error_code="429")

    # Nothing was collected and nothing was considered, so the three standing claims survive: a
    # rate limit is not evidence that a product stopped having an onset.
    assert _go(catalogue, secret_file, _RateLimited()) == 2
    assert len(_claims(catalogue)) == 3


def test_the_series_is_stored_under_the_product_and_the_api(catalogue, secret_file):
    fetcher = _FakeFetcher({"oy:A1": {"search_trend": rising(2023, 5), "shopping_insight": rising(2023, 5)}})
    _go(catalogue, secret_file, fetcher)
    engine = sa.create_engine(catalogue)
    with engine.begin() as conn:
        apis = conn.execute(sa.select(naver_launch_series.c.api).distinct()).scalars().all()
        keys = (
            conn.execute(
                sa.select(naver_launch_series.c.series_key)
                .where(naver_launch_series.c.api == "search_trend")
                .distinct()
            )
            .scalars()
            .all()
        )
        shopping_keys = (
            conn.execute(
                sa.select(naver_launch_series.c.series_key)
                .where(naver_launch_series.c.api == "shopping_insight")
                .distinct()
            )
            .scalars()
            .all()
        )
    engine.dispose()
    assert set(apis) == {"search_trend", "shopping_insight"}
    assert keys == [""], "one keyword group means one series, keyed by nothing but the product"
    assert len(shopping_keys) == 3, "one series per term, keyed by the term"


def _terms_file(tmp_path: Path, keep: Collection[str] = (), *, extra: Collection[str] = ()) -> Path:
    """A term list built from the fixture rather than typed here: the terms are Korean data values
    and `tool/checks/lang` allows those under `tests/**/fixtures/`, not in a test module."""
    raw = json.loads(TERMS.read_text(encoding="utf-8"))
    products = {ref: terms for ref, terms in raw["products"].items() if not keep or ref in keep}
    borrowed = next(iter(raw["products"].values()))
    for ref in extra:
        products[ref] = borrowed
    path = tmp_path / "launch_terms.json"
    path.write_text(json.dumps({"generic": [], "products": products}, ensure_ascii=False), encoding="utf-8")
    return path


def test_the_pass_gives_the_real_fetcher_the_launch_budget(catalogue, secret_file, monkeypatch):
    """B1: the one wire that no run test could see, because every one of them injects a fake.

    `cli.run` builds the real `HttpFetcher` when no fetcher is passed, and a fetcher built without a
    budget takes `MAX_REQUESTS_PER_RUN` (200) -- under which a filled 248-product list spends its
    budget at the 101st product, stops `partial`, never reaches the vacated-claims sweep, and makes
    `contracts/entrypoints.md`'s "1,500 requests a run" false of the code.

    The real class is constructed here, with an httpx transport instead of a socket, so the default
    in `HttpFetcher.__init__` is the thing under test rather than a stand-in for it."""
    built: list[HttpFetcher] = []
    real = cli.HttpFetcher

    def spy(client_id: str, client_secret: str, **kwargs: Any) -> HttpFetcher:
        fetcher = real(
            client_id,
            client_secret,
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"results": []})),
            **kwargs,
        )
        built.append(fetcher)
        return fetcher

    monkeypatch.setattr(cli, "HttpFetcher", spy)
    run(
        "launch_onset",
        database_url=catalogue,
        secrets_path=secret_file,
        captured_at=AT,
        terms_path=TERMS,
    )
    assert [f.budget for f in built] == [scope.LAUNCH_MAX_REQUESTS_PER_RUN]
    assert scope.LAUNCH_MAX_REQUESTS_PER_RUN != scope.MAX_REQUESTS_PER_RUN, "the test would be vacuous"


def test_the_other_datasets_keep_the_house_budget(catalogue, secret_file, monkeypatch):
    # The counterpart: giving launch_onset its own ceiling must not raise everyone else's.
    built: list[HttpFetcher] = []
    real = cli.HttpFetcher

    def spy(client_id: str, client_secret: str, **kwargs: Any) -> HttpFetcher:
        fetcher = real(
            client_id,
            client_secret,
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"results": []})),
            **kwargs,
        )
        built.append(fetcher)
        return fetcher

    monkeypatch.setattr(cli, "HttpFetcher", spy)
    run("datalab", database_url=catalogue, secrets_path=secret_file, captured_at=AT)
    assert [f.budget for f in built] == [scope.MAX_REQUESTS_PER_RUN]


def test_a_term_list_naming_a_ref_the_catalogue_does_not_hold_spares_no_claim(
    catalogue, secret_file, tmp_path, monkeypatch
):
    """F4: the one hole in the DELETE duty. Such a ref is skipped before its per-product withdrawal,
    so the only thing that could ever withdraw it is the vacated sweep -- and the sweep spared it,
    because the term list still named it. What the pass hands the sweep is the set it can speak
    for, which is what is checked here."""
    seen: list[list[str]] = []
    real = storage_db.withdraw_vacated_launch_claims

    def spy(connection: Any, *, axis: str, considered: Sequence[str]) -> int:
        seen.append(list(considered))
        return real(connection, axis=axis, considered=considered)

    monkeypatch.setattr(storage_db, "withdraw_vacated_launch_claims", spy)
    terms = _terms_file(tmp_path, extra=["oy:NOT_IN_THE_CATALOGUE"])
    code = _go(
        catalogue,
        secret_file,
        _FakeFetcher({"oy:A1": {"search_trend": rising(2023, 5)}}),
        terms=terms,
    )
    assert seen, "a pass that ran to the end must reach the sweep"
    assert "oy:NOT_IN_THE_CATALOGUE" not in seen[0]
    assert {"oy:A1", "oy:B1", "oy:C1"} <= set(seen[0])
    assert code == 1, "the unreachable ref is reported, not swallowed"


def test_one_products_4xx_is_partial_even_when_it_is_the_whole_term_list(catalogue, secret_file, tmp_path):
    # F5: blocked (2) is what collector_health reads as refused, and one product's 4xx is not a
    # refusal however short the term list is. Only 401/429/a spent budget stop the whole pass.
    terms = _terms_file(tmp_path, keep=["oy:A1"])
    assert _go(catalogue, secret_file, _FakeFetcher({}, fails=["oy:A1"]), terms=terms) == 1
