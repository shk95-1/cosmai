"""The entry point of the `analyze aggregate` stage. `analyze all` of #5 calls run(conn, ...) in one line."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date
from typing import Any, LiteralString

import psycopg

from analysis.aggregate import ROLLUP_SCOPE, WISH_SCOPES, RuleAggregator
from analysis.aggregate.ranking import WRITE_BATCH, run_ranking
from analysis.types import DenominatorRow, MetricsNeedRow, MetricsWishRow, NeedMentionRow, WishMentionRow

__all__ = ["load_denominators", "load_needs", "load_wishes", "population_of", "run", "scopes_for"]

NEED_COLUMNS = (
    "src, site, ref, product_ref, source_product_key, category, lexicon_category, need_key, "
    "aspect_scope, polarity, strength, rating, observed_at, observed_at_resolution, month, sentence, "
    "kind, marker, polarity_reason, extractor_version, polarity_version"
)
WISH_COLUMNS = (
    "src, ref, video_id, channel_id, channel_is_brand_owner, product_ref, observed_at, "
    "observed_at_resolution, month, wish_class, brand, format, attribute, marker, sentence, "
    "like_count, extractor_version"
)
DENOMINATOR_COLUMNS = (
    "source, product_key, captured_at, category, site_review_count, low_collected, low_complete, site_low_est"
)

NEED_SQL: LiteralString = """
INSERT INTO metrics_need
  (run_id, scope, need_key, month, product_ref, neg, pos, yt_neg, yt_pos, unresolved, unresolved_new,
   low_share, population_share_pct, low_mentioning, denom_low, denom_site, strength_mean,
   strength_low_rating_ratio, persist_months, persist_months_total, persist_products,
   persist_products_total, aspect_scope)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (run_id, scope, need_key, month, product_ref) DO UPDATE
SET neg = EXCLUDED.neg, pos = EXCLUDED.pos, yt_neg = EXCLUDED.yt_neg, yt_pos = EXCLUDED.yt_pos,
    unresolved = EXCLUDED.unresolved, unresolved_new = EXCLUDED.unresolved_new,
    low_share = EXCLUDED.low_share, population_share_pct = EXCLUDED.population_share_pct,
    low_mentioning = EXCLUDED.low_mentioning, denom_low = EXCLUDED.denom_low,
    denom_site = EXCLUDED.denom_site, strength_mean = EXCLUDED.strength_mean,
    strength_low_rating_ratio = EXCLUDED.strength_low_rating_ratio,
    persist_months = EXCLUDED.persist_months, persist_months_total = EXCLUDED.persist_months_total,
    persist_products = EXCLUDED.persist_products,
    persist_products_total = EXCLUDED.persist_products_total, aspect_scope = EXCLUDED.aspect_scope
"""
WISH_SQL: LiteralString = """
INSERT INTO metrics_wish
  (run_id, scope, format, attribute, brand, mentions, channels, videos, months_present, first_month,
   last_month, like_sum, like_cap_sum, max_like, example)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (run_id, scope, format, attribute, brand) DO UPDATE
SET mentions = EXCLUDED.mentions, channels = EXCLUDED.channels, videos = EXCLUDED.videos,
    months_present = EXCLUDED.months_present, first_month = EXCLUDED.first_month,
    last_month = EXCLUDED.last_month, like_sum = EXCLUDED.like_sum,
    like_cap_sum = EXCLUDED.like_cap_sum, max_like = EXCLUDED.max_like, example = EXCLUDED.example
"""


def _float(value: Any) -> float | None:
    """A numeric column comes back as Decimal, but the contract's row type is float (interfaces.md)."""
    return None if value is None else float(value)


def load_needs(cur: psycopg.Cursor[Any], population: Sequence[str]) -> list[NeedMentionRow]:
    cur.execute(
        f"SELECT {NEED_COLUMNS} FROM need_mention "  # noqa: S608 - the columns are constants of this module
        "WHERE extractor_version = ANY(%s) ORDER BY mention_id",
        (list(population),),
    )
    return [
        NeedMentionRow(
            src=r[0], site=r[1], ref=r[2], product_ref=r[3], source_product_key=r[4], category=r[5],
            lexicon_category=r[6], need_key=r[7], aspect_scope=r[8], polarity=r[9],
            strength=_float(r[10]), rating=_float(r[11]), observed_at=r[12],
            observed_at_resolution=r[13], month=r[14], sentence=r[15], kind=r[16], marker=r[17],
            polarity_reason=r[18], extractor_version=r[19], polarity_version=r[20],
        )
        for r in cur.fetchall()
    ]  # fmt: skip


def load_wishes(cur: psycopg.Cursor[Any], population: Sequence[str]) -> list[WishMentionRow]:
    cur.execute(
        f"SELECT {WISH_COLUMNS} FROM wish_mention "  # noqa: S608
        "WHERE extractor_version = ANY(%s) ORDER BY mention_id",
        (list(population),),
    )
    return [
        WishMentionRow(
            src=r[0], ref=r[1], video_id=r[2], channel_id=r[3], channel_is_brand_owner=r[4],
            product_ref=r[5], observed_at=r[6], observed_at_resolution=r[7], month=r[8],
            wish_class=r[9], brand=r[10], format=r[11], attribute=r[12], marker=r[13],
            sentence=r[14], like_count=r[15], extractor_version=r[16],
        )
        for r in cur.fetchall()
    ]  # fmt: skip


def load_denominators(cur: psycopg.Cursor[Any]) -> list[DenominatorRow]:
    cur.execute(f"SELECT {DENOMINATOR_COLUMNS} FROM product_denominator")  # noqa: S608
    return [
        DenominatorRow(
            source=r[0], product_key=r[1], captured_at=r[2], category=r[3], site_review_count=r[4],
            low_collected=r[5], low_complete=r[6], site_low_est=_float(r[7]),
        )
        for r in cur.fetchall()
    ]  # fmt: skip


def load_canonical(cur: psycopg.Cursor[Any]) -> dict[str, str]:
    cur.execute("SELECT need_key, canonical FROM need_key")
    return dict(cur.fetchall())


def population_of(cur: psycopg.Cursor[Any], extractors: Sequence[str] | None) -> tuple[str, ...]:
    """What was aggregated has to stay on the run — two extractor versions are not mixed into one scope
    quietly."""
    cur.execute(
        "SELECT extractor_version FROM need_mention "
        "UNION SELECT extractor_version FROM wish_mention ORDER BY 1"
    )
    available = [v for (v,) in cur.fetchall()]
    if extractors is None:
        if len(available) > 1:
            raise ValueError(
                f"need_mention holds {len(available)} extractor_version(s) ({', '.join(available)}); "
                "name the population with extractors=(...)"
            )
        return tuple(available)
    unknown = sorted(set(extractors) - set(available))
    if unknown:
        raise LookupError(f"no mentions with extractor_version {', '.join(unknown)}")
    return tuple(sorted(set(extractors)))


def _versions(
    aggregator: RuleAggregator, cur: psycopg.Cursor[Any], population: Sequence[str]
) -> dict[str, Any]:
    """versions is the version of the output this run actually read — the only clue for reproducing an
    analysis (versioning.md)."""
    cur.execute(
        "SELECT DISTINCT extractor_version, polarity_version FROM need_mention "
        "WHERE extractor_version = ANY(%s)",
        (list(population),),
    )
    pairs = sorted(cur.fetchall())
    cur.execute("SELECT max(version) FROM entity_lexicon")
    lexicon = cur.fetchone()
    cur.execute("SELECT DISTINCT linker_version FROM product_ref")
    return {
        "linker": ";".join(sorted(v for (v,) in cur.fetchall())) or None,
        # Exactly the population this run picked — later "what was aggregated" is answered by this one value.
        "extractor": ";".join(population) or None,
        "polarity": ";".join(sorted({p for _, p in pairs})) or None,
        "aggregate": aggregator.version,
        "lexicon": lexicon[0] if lexicon else None,
    }


def _run_id(cur: psycopg.Cursor[Any], note: str, versions: dict[str, Any]) -> int:
    """Found by note and created only when there is none — piling up runs on a rerun makes idempotence
    unobservable."""
    cur.execute("SELECT run_id FROM analysis_run WHERE note = %s ORDER BY run_id LIMIT 1", (note,))
    found = cur.fetchone()
    payload = json.dumps(versions, ensure_ascii=False)
    if found:
        cur.execute(
            "UPDATE analysis_run SET status = 'running', finished_at = NULL, versions = %s::jsonb "
            "WHERE run_id = %s",
            (payload, found[0]),
        )
        return int(found[0])
    cur.execute(
        "INSERT INTO analysis_run (status, versions, note) VALUES ('running', %s::jsonb, %s) "
        "RETURNING run_id",
        (payload, note),
    )
    created = cur.fetchone()
    assert created is not None
    return int(created[0])


def _need_values(row: MetricsNeedRow, run_id: int) -> tuple[Any, ...]:
    return (
        run_id, row.scope, row.need_key, row.month, row.product_ref, row.neg, row.pos, row.yt_neg,
        row.yt_pos, row.unresolved, row.unresolved_new, row.low_share, row.population_share_pct,
        row.low_mentioning, row.denom_low, row.denom_site, row.strength_mean,
        row.strength_low_rating_ratio, row.persist_months, row.persist_months_total,
        row.persist_products, row.persist_products_total, row.aspect_scope,
    )  # fmt: skip


def _wish_values(row: MetricsWishRow, run_id: int) -> tuple[Any, ...]:
    return (
        run_id, row.scope, row.format, row.attribute, row.brand, row.mentions, row.channels,
        row.videos, row.months_present, row.first_month, row.last_month, row.like_sum,
        row.like_cap_sum, row.max_like, row.example,
    )  # fmt: skip


def _write(
    conn: psycopg.Connection[Any], statement: LiteralString, rows: list[tuple[Any, ...]], batch: int
) -> None:
    """The same shape as _write in ranking.py — pushing every scope in one executemany goes past 60s."""
    for start in range(0, len(rows), batch):
        with conn.cursor() as cur:
            cur.executemany(statement, rows[start : start + batch])
        conn.commit()


def scopes_for(scope: str | None, mentions: Sequence[NeedMentionRow]) -> list[str]:
    """Which metrics_need scopes this run writes — `--scope` names either axis (#38).

    polarity scopes by `lexicon_category`, this stage by the source category, and the two are not the
    same string: `--scope 선블록` labelled 13,857 rows whose `category` was `01 > 선케어 > 선블록`
    (run 16), so filtering on the given string alone aggregated none of them. A lexicon value expands
    to the source categories its own labels sit on — the mapping is not invertible from
    `needs.category_map` alone (an unlisted leaf maps to itself, and a name_keyword label carries no
    source category at all), so the answer comes from the run's own population.

    The given string is kept whatever axis it belongs to: a source category names exactly the scope it
    always did, and a label whose rows carry no source category still writes nothing under it — which
    is what `_amend_silent_scope` (analysis/pipeline.py) reports rather than closing quietly.
    """
    if scope:
        # The counting is unchanged: the expansion only picks which category to write under, and what is
        # counted inside it is the same as what a run without a scope writes to that category (need_metrics
        # filters on category alone).
        return sorted({m.category for m in mentions if m.category and m.lexicon_category == scope} | {scope})
    return sorted({m.category for m in mentions if m.category} | {ROLLUP_SCOPE})


def run(
    conn: psycopg.Connection[Any],
    scope: str | None = None,
    run_id: int | None = None,
    commerce_schema: str | None = None,
    captured_at: date | None = None,
    extractors: Sequence[str] | None = None,
    batch: int = WRITE_BATCH,
) -> int:
    """Given a run_id it writes to that run and does not touch the status twice — #5 ties three stages into
    one run."""
    with conn.cursor() as cur:
        aggregator = RuleAggregator(canonical=load_canonical(cur))
        population = population_of(cur, extractors)
    conn.rollback()
    if commerce_schema is not None:
        run_ranking(conn, aggregator.version, captured_at or date.today(), commerce_schema)
    # One table is read and closed right away: assembling the rows after fetchall is inside the transaction
    # too, and for that while the session is idle in transaction (the 15s in db/bootstrap.sql is sized to a
    # statement, not to a job).
    with conn.cursor() as cur:
        mentions = load_needs(cur, population)
    conn.commit()
    with conn.cursor() as cur:
        wishes = load_wishes(cur, population)
    conn.commit()
    with conn.cursor() as cur:
        denominators = load_denominators(cur)
    conn.commit()
    owned = run_id is None
    if run_id is None:
        with conn.cursor() as cur:
            note = f"aggregate:{aggregator.version}:{scope or ROLLUP_SCOPE}:{'+'.join(population)}"
            run_id = _run_id(cur, note, _versions(aggregator, cur, population))
        conn.commit()

    scopes = scopes_for(scope, mentions)
    need_rows = [r for s in scopes for r in aggregator.need_metrics(mentions, denominators, s)]
    # A wish scope is not a category axis — narrowing with --scope still emits the same three scopes.
    wish_rows = [r for s in WISH_SCOPES for r in aggregator.wish_metrics(wishes, s)]

    # Every row of this run is made by this run — deleting and reinserting is what keeps a vanished key out.
    with conn.cursor() as cur:
        cur.execute("DELETE FROM metrics_need WHERE run_id = %s", (run_id,))
        cur.execute("DELETE FROM metrics_wish WHERE run_id = %s", (run_id,))
    conn.commit()
    _write(conn, NEED_SQL, [_need_values(r, run_id) for r in need_rows], batch)
    _write(conn, WISH_SQL, [_wish_values(r, run_id) for r in wish_rows], batch)
    if owned:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE analysis_run SET status = 'ok', finished_at = now() WHERE run_id = %s",
                (run_id,),
            )
        conn.commit()
    return run_id
