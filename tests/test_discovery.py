"""Real source behavior plus falsification of independence and grounding claims (#323)."""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path

import pytest

from analysis.discovery.alternatives import identity_relation
from analysis.discovery.core import fingerprint, freeze, select

FIXTURES = Path(__file__).parent / "fixtures" / "discovery"
WINNER = "oily-skin-foundation-without-diy-blending"


def market_review(review):
    review = deepcopy(review)
    review["market_reviews"] = {WINNER: json.loads((FIXTURES / "first_case_alternatives.json").read_text())}
    return review


def test_real_alternatives_reject_the_provisional_lead_without_inventing_a_satisfactory_product(case):
    sample, review = case
    result = select(sample, market_review(review))
    assert result["selected"] is None
    assert result["status"] == "no_qualified_candidate"
    candidate = next(c for c in result["candidates"] if c["candidate"] == WINNER)
    assert (
        candidate["support"]
        == next(c for c in select(*case)["candidates"] if c["candidate"] == WINNER)["support"]
    )
    assert candidate["counterevidence"]
    assessment = candidate["market_review"]
    assert assessment["decision"]["basis"] == "insufficient_common_requirement"
    assert len(assessment["requirements"]) == 5
    assert len(assessment["alternatives"]) == 4
    assert all(a["identity"]["level"] == "listing" for a in assessment["alternatives"])
    assert all(any(c["status"] == "unknown" for c in a["comparisons"]) for a in assessment["alternatives"])
    assert "market review" in candidate["rejection_reasons"][-1]
    assert result == select(sample, market_review(review))


def test_uniform_followup_cannot_count_two_dated_cleanser_reviews_as_two_users():
    sample = json.loads((FIXTURES / "followup_sample.json").read_text())
    review = json.loads((FIXTURES / "followup_review.json").read_text())
    assert len(sample["documents"]) == len(review["observations"]) == 208
    assert sample["manifest"]["per_cue"] == 12
    assert not sample["manifest"]["failures"]
    result = select(sample, review)
    assert result["status"] == "no_qualified_candidate"
    cleanser = next(
        c
        for c in result["candidates"]
        if c["candidate"] == "facial-cleanser-without-import-or-unwanted-bumps"
    )
    assert len(cleanser["support"]) == len(cleanser["dates"]) == 2
    assert cleanser["source_local_users"] == 1
    assert cleanser["rejection_reasons"] == ["fewer than two attributable source-local users"]
    # The initial group is still present, with its market rejection; it was not silently
    # removed or relabelled to produce a preferred new winner after expansion.
    foundation = next(c for c in result["candidates"] if c["candidate"] == WINNER)
    assert foundation["market_review"]["decision"]["status"] == "reject"
    assert len(result["candidates"]) == 25


@pytest.mark.parametrize(
    "change",
    ["snapshot", "support", "span", "missing_user", "comparison", "line_shade", "unknown_gap", "brand_proof"],
)
def test_market_feedback_refuses_evidence_drift_and_false_gap_or_variant_proof(case, change):
    sample, review = case
    review = market_review(review)
    assessment = review["market_reviews"][WINNER]
    if change in {"snapshot", "support"}:
        assessment[change + "_sha256"] = "wrong"
    elif change == "span":
        assessment["requirements"][0]["span"] = "unsupported exact shade 21C"
    elif change == "missing_user":
        assessment["requirements"] = assessment["requirements"][:3]
    elif change == "comparison":
        assessment["alternatives"][0]["comparisons"].pop()
    elif change == "line_shade":
        comparison = assessment["alternatives"][0]["comparisons"][0]
        comparison["status"] = "met"
        comparison["evidence"] = assessment["alternatives"][0]["source"]
    elif change == "unknown_gap":
        assessment["decision"]["status"] = "retain_for_technical_review"
    else:
        assessment["decision"]["basis"] = "existing_alternative"
    with pytest.raises(ValueError):
        select(sample, review)


def test_product_line_options_and_multi_variant_listings_never_collapse_to_identity():
    line = {"source": "shop", "product_id": "base", "level": "line"}
    listing = {**line, "level": "listing", "variant_keys": ["unscented-30", "lavender-50"]}
    option = {**line, "level": "variant", "variant_key": "unscented-30"}
    other = {**option, "variant_key": "lavender-50"}
    assert identity_relation(line, option) == "unresolved"
    assert identity_relation(listing, option) == "listing_contains_variant"
    assert identity_relation(listing, other) == "listing_contains_variant"
    assert identity_relation(option, other) == "distinct"  # shared listing is not transitive equality
    assert identity_relation(option, option) == "same_variant"
    assert identity_relation(option, {**option, "source": "other-shop"}) == "unresolved"
    with pytest.raises(ValueError, match="option/SKU"):
        identity_relation(line, {**line, "level": "variant"})


@pytest.mark.parametrize(
    "attribute,values",
    [
        ("spf", [30, 50]),
        ("scent", ["unscented", "lavender"]),
        ("generation", ["original", "renewed"]),
        ("shade", ["21C", "21N"]),
    ],
)
def test_known_conflicts_defeat_even_equal_source_option_keys(attribute, values):
    option = {"source": "shop", "product_id": "base", "level": "variant", "variant_key": "shared"}
    assert (
        identity_relation(
            {**option, "attributes": {attribute: values[0]}},
            {**option, "attributes": {attribute: values[1]}},
        )
        == "distinct"
    )


@pytest.fixture
def case():
    return (
        json.loads((FIXTURES / "first_case_sample.json").read_text()),
        json.loads((FIXTURES / "first_case_review.json").read_text()),
    )


def subset(sample, review, ids):
    sample = freeze(sample["manifest"], [deepcopy(d) for d in sample["documents"] if d["doc_id"] in ids])
    review = {
        **deepcopy(review),
        "snapshot_sha256": sample["snapshot_sha256"],
        "observations": [deepcopy(o) for o in review["observations"] if o["doc_id"] in ids],
    }
    return sample, review


def test_reviewed_small_case_has_specific_behavior_not_a_growth_or_topic_gate(case):
    sample, review = case
    result = select(sample, review)
    assert result["selected"] == WINNER
    winner = next(c for c in result["candidates"] if c["candidate"] == WINNER)
    assert winner["source_local_users"] == 2
    assert winner["sources"] == ["glowpick"]
    assert len(winner["support"]) == 2
    assert winner["counterevidence"]  # a satisfied cushion user must not disappear from the output
    assert winner["technical"]["status"] == "unassessed"
    assert winner["feasibility"]["status"] == "unassessed"
    assert len(result["candidates"]) == 18
    assert all(c["rejection_reasons"] for c in result["candidates"] if c["candidate"] != WINNER)
    assert Counter(o["disposition"] for o in review["observations"]) == {
        "observed": 19,
        "ambiguous": 23,
        "satisfied": 35,
        "wish": 22,
        "promotion": 5,
        "off_topic": 5,
    }
    assert result == select(sample, review)  # frozen research inputs replay identically, offline


def test_promotion_ambiguous_and_copied_recommendations_do_not_corroborate(case):
    result = select(*case)
    reasons = {r["doc_id"]: r["reason"] for r in result["rejected_observations"]}
    assert reasons["commerce_review:oliveyoung:65795832"] == "normalized duplicate; no independent support"
    assert reasons["youtube_comment:UgxJ85h2BGtvzw9WsnR4AaABAg"].startswith("wish:")
    assert reasons["youtube_comment:UgzBVTR-SrF-S3v9rUF4AaABAg"].startswith("promotion:")
    assert reasons["commerce_review:glowpick:7891450"].startswith("ambiguous:")


def test_single_observer_does_not_turn_repeated_reviews_into_independent_demand(case):
    sample, review = case
    docs = [
        d
        for d in sample["documents"]
        if d["doc_id"] in {"commerce_review:glowpick:7858181", "commerce_review:glowpick:7860436"}
    ]
    docs[1]["author_hash"] = docs[0]["author_hash"]
    sample = freeze(sample["manifest"], sample["documents"])
    review["snapshot_sha256"] = sample["snapshot_sha256"]
    result = select(sample, review)
    assert result["status"] == "no_qualified_candidate"
    assert result["selected"] is None


@pytest.mark.parametrize("change", ["unknown_author", "copied_positive"])
def test_unattributed_or_copied_support_cannot_select_a_need(case, change):
    sample, review = case
    ids = {"commerce_review:glowpick:7858181", "commerce_review:glowpick:7860436"}
    sample, review = subset(sample, review, ids)
    if change == "unknown_author":
        for d in sample["documents"]:
            d["author_hash"] = None
    else:
        sample["documents"][1]["text"] = sample["documents"][0]["text"]
        review["observations"][1] = {
            **deepcopy(review["observations"][0]),
            "doc_id": sample["documents"][1]["doc_id"],
        }
    sample = freeze(sample["manifest"], sample["documents"])
    review["snapshot_sha256"] = sample["snapshot_sha256"]
    assert select(sample, review)["selected"] is None


@pytest.mark.parametrize(
    "change",
    [
        "mutated_text",
        "wrong_snapshot",
        "fabricated_burden",
        "missing_review",
        "missing_recurrence",
        "raw_author",
    ],
)
def test_refuses_ungrounded_or_incomplete_research(case, change):
    sample, review = case
    positive = next(o for o in review["observations"] if o["disposition"] == "observed" and o["recurring"])
    if change == "mutated_text":
        sample["documents"][0]["text"] += " altered"
    elif change == "wrong_snapshot":
        review["snapshot_sha256"] = "wrong"
    elif change == "fabricated_burden":
        positive["burden"]["span"] = "paid a documented premium of 500 dollars"
    elif change == "missing_review":
        review["observations"].pop()
    elif change == "missing_recurrence":
        positive.pop("recurrence_span")
    else:
        sample["documents"][0]["author_hash"] = "UCraw-channel"
        sample["snapshot_sha256"] = fingerprint({k: v for k, v in sample.items() if k != "snapshot_sha256"})
    with pytest.raises(ValueError):
        select(sample, review)


def test_explicit_technical_conflict_rejects_candidate_and_keeps_trail(case):
    sample, review = case
    review["candidates"][WINNER]["technical"] = {
        "status": "contradictory",
        "reason": "Applicability disputed; requires review",
        "references": [],
    }
    result = select(sample, review)
    assert result["selected"] is None
    c = next(c for c in result["candidates"] if c["candidate"] == WINNER)
    assert "preliminary technical/feasibility conflict" in c["rejection_reasons"]
    assert c["support"]


def test_initial_uniformly_smaller_sample_honestly_has_no_qualified_candidate(case):
    sample, review = case
    # First three MD5-ordered IDs from every source/cue; the stored expanded sample includes
    # each initial row. Reconstruct that initial bound without manually selecting a problem.
    import hashlib

    ids = set()
    for source in {d["source"] for d in sample["documents"]}:
        for cue in sample["manifest"]["cues"]:
            rows = [d for d in sample["documents"] if d["source"] == source and cue["cue"] in d["cues"]]
            rows.sort(
                key=lambda d: (hashlib.md5(d["source_item_id"].encode()).hexdigest(), d["source_item_id"])
            )
            ids.update(d["doc_id"] for d in rows[:3])
    initial, reviews = subset(sample, review, ids)
    assert len(initial["documents"]) == 55
    assert select(initial, reviews)["status"] == "no_qualified_candidate"


def test_offline_command_writes_complete_private_result(case, tmp_path):
    output = tmp_path / "selection.json"
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "cosmai.cli",
            "discover",
            "select",
            "--sample",
            str(FIXTURES / "first_case_sample.json"),
            "--review",
            str(FIXTURES / "first_case_review.json"),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    assert json.loads(output.read_text())["selected"] == WINNER
    assert output.stat().st_mode & 0o777 == 0o600
    assert "author_hash" not in out.stdout


@pytest.mark.postgres
def test_export_deduplicates_latest_snapshots_and_enforces_read_only(database_url_for_tests, monkeypatch):
    import psycopg

    from analysis.discovery import sample as adapter
    from db.runtime import connect

    # Per-test tables, never production names. Use the actual exporter SQL after substituting
    # only schema/table names so DISTINCT ON, dates, hashes and read-only mode are exercised.
    with connect(database_url_for_tests) as conn:
        conn.execute(
            "CREATE TABLE corpus_document (snapshot_id text, source text, source_item_id text, "
            "text text, source_metadata jsonb, published_at timestamptz, url text, "
            "parent_item_id text, source_run text, collected_at timestamptz)"
        )
        conn.execute(
            "INSERT INTO corpus_document VALUES "
            "('old','youtube_comment','one','old discontinued text', '{}', '2026-01-01', "
            "NULL,NULL,'archive','2026-01-01'), "
            "('new','youtube_comment','one','new discontinued text', "
            "'{\"author_channel_hash\": \"aaaaaaaaaaaaaaaaaaaaaaaa\"}', '2026-01-01', "
            "'https://www.youtube.com/watch?v=test',NULL,'live','2026-02-01')"
        )
        conn.commit()
    monkeypatch.setattr(adapter, "COMMERCE", ())
    monkeypatch.setattr(
        adapter, "YOUTUBE_BASE", adapter.YOUTUBE_BASE.replace("needs.corpus_document", "corpus_document")
    )
    monkeypatch.setattr(adapter, "runtime_url", lambda: database_url_for_tests)
    original_connect = adapter.connect
    read_only = []

    class CheckedConnection:
        def __init__(self, conn):
            self.conn = conn

        @property
        def autocommit(self):
            return self.conn.autocommit

        @autocommit.setter
        def autocommit(self, value):
            self.conn.autocommit = value

        def __enter__(self):
            self.conn.__enter__()
            return self

        def __exit__(self, *args):
            return self.conn.__exit__(*args)

        def execute(self, *args, **kwargs):
            return self.conn.execute(*args, **kwargs)

        def cursor(self, *args, **kwargs):
            with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                self.conn.execute("INSERT INTO corpus_document (snapshot_id) VALUES ('forbidden')")
            read_only.append(True)
            return self.conn.cursor(*args, **kwargs)

    def checked_connect(url):
        return CheckedConnection(original_connect(url))

    monkeypatch.setattr(adapter, "connect", checked_connect)
    exported = adapter.sample_local(3)
    assert read_only
    assert exported["manifest"]["failures"] == []
    assert exported["manifest"]["coverage"][0]["documents"] == 1
    assert len(exported["documents"]) == 1
    assert exported["documents"][0]["text"] == "new discontinued text"
    assert exported["documents"][0]["source_run"] == "live"


def test_access_failures_are_reported_without_driver_error_text(monkeypatch):
    from analysis.discovery import sample as adapter

    class BrokenConnection:
        autocommit = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, *args):
            pass

        def cursor(self, *args, **kwargs):
            raise RuntimeError("sensitive connection detail must not enter the artifact")

    monkeypatch.setattr(adapter, "runtime_url", lambda: "unused")
    monkeypatch.setattr(adapter, "connect", lambda _: BrokenConnection())
    exported = adapter.sample_local(3)
    assert len(exported["manifest"]["failures"]) == 4
    assert not exported["documents"]
    assert "sensitive" not in json.dumps(exported)
    review = {
        "snapshot_sha256": exported["snapshot_sha256"],
        "reviewer": "test",
        "interventions": [],
        "observations": [],
        "candidates": {},
    }
    result = select(exported, review)
    assert result["status"] == "no_qualified_candidate"
    assert result["manifest"]["failures"]


@pytest.mark.parametrize(
    "times",
    [
        ("2026-08-20T08:00:00+00:00", "2026-08-20T23:00:00+00:00"),
        ("2026-08-20T08:00:00-05:00", "2026-08-21T02:00:00+09:00"),
    ],
)
def test_timestamp_or_timezone_differences_do_not_establish_multiple_days(case, times):
    sample, review = case
    sample, review = subset(
        sample, review, {"commerce_review:glowpick:7858181", "commerce_review:glowpick:7860436"}
    )
    for d, timestamp in zip(sample["documents"], times, strict=True):
        d["published_at"] = timestamp
    for o in review["observations"]:
        o["recurring"] = False
    sample = freeze(sample["manifest"], sample["documents"])
    review["snapshot_sha256"] = sample["snapshot_sha256"]
    result = select(sample, review)
    assert result["status"] == "no_qualified_candidate"
    winner = next(c for c in result["candidates"] if c["candidate"] == WINNER)
    assert winner["dates"] == ["2026-08-20"]
    assert "repeatability unestablished" in winner["rejection_reasons"]
