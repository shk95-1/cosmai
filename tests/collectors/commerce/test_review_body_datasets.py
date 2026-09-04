"""`Source.review_body_datasets` 는 `parse()` 가 실제로 하는 일과 같아야 한다.

이 선언이 왜 있는가: `trend_radar.review` 에는 `run_id` 가 없다. needs 의 `collection_lineage`
(slopindustries/cosmai#144)는 리뷰 한 줄에서 그것을 걷은 run 으로 `(captured_at, sources, datasets)`
셋으로만 건너가고, 그중 `datasets` 를 틀리게 읽으면 **진짜 단일 매치가 조용히 '미상' 으로
오분류된다**. 그것이 실제로 났다: dataset 을 사이트와 무관하게 `{review, review_low}` 로 좁혔더니
glowpick 리뷰 3,597건 중 2,284건(63.5퍼센트)이 미상이 됐다 -- glowpick 의 `parse()` 는 dataset 으로
게이트하지 않아 매시 ranking 런이 리뷰 본문을 쓰기 때문이다.

So the declaration lives next to the source rather than in SQL, and this file **cries when the
declaration and the code drift apart**. What it measures here is behavior, not documentation: it feeds
recorded fixtures straight into `parse()`, collects which datasets actually produce a `ReviewRecord`,
and checks that against the declaration. If a gate changes (or disappears), that spot goes red here.

Whether the view reflects this declaration is guarded separately by `tests/test_collection_lineage_view.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

import collectors.commerce.sources  # noqa: F401  -- registration is an import side effect
from collectors.commerce.contract import Fetch, Payload, Source
from collectors.commerce.models import Dataset, ReviewRecord
from collectors.commerce.registry import SOURCES

AT = datetime(2026, 8, 18, 9, tzinfo=UTC)
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _payload(fetch: Fetch, body: bytes) -> Payload:
    return Payload(
        fetch=fetch, status=200, body=body, final_url=fetch.url, headers={}, elapsed_ms=1, captured_at=AT
    )


def _review_count(source: Source, fetch: Fetch, body: bytes) -> tuple[int, bool]:
    """(the ReviewRecord count that parse produced, whether the parse succeeded).

    Feeding the parser a fixture from the wrong dataset can make it throw -- that isn't what this
    test is about, it just means the combination is meaningless. But everything throwing and
    quietly turning green must be prevented, so the count of successful parses is counted separately
    below.
    """
    try:
        out = source.parse(_payload(fetch, body))
    except Exception:  # noqa: BLE001 -- the reason is in the docstring above; success rides in the return.
        return 0, False
    return len([r for r in out.records if isinstance(r, ReviewRecord)]), True


def _datasets_that_write_bodies(source: Source, bodies: list[bytes]) -> tuple[set[Dataset], int]:
    """The datasets on which that source produces review bodies, and the count of successful parses.

    Looks at both the seed step and the one follow step the seed produced -- oliveyoung's and
    daisomall's review bodies come not from the seed (the ranking page) but from the review endpoint
    after it. Which dataset it gets recorded under is **the run's dataset**, i.e. the value that
    called `seeds(...)` -- that is what `trend_radar.run.datasets` records (`log.start` in
    collectors/commerce/cli.py).
    """
    found: set[Dataset] = set()
    parsed = 0
    for dataset in sorted(source.datasets, key=lambda d: d.value):
        for body in bodies:
            for seed in source.seeds(dataset)[:1]:
                count, ok = _review_count(source, seed, body)
                parsed += ok
                if count:
                    found.add(dataset)
                if not ok:
                    continue
                for follow in source.parse(_payload(seed, body)).follow:
                    for other in bodies:
                        count, ok = _review_count(source, follow, other)
                        if count:
                            found.add(dataset)
    return found, parsed


@pytest.fixture(scope="module")
def replayed() -> dict[str, tuple[set[Dataset], int]]:
    out: dict[str, tuple[set[Dataset], int]] = {}
    for key, cls in sorted(SOURCES.items()):
        bodies = [p.read_bytes() for p in sorted((FIXTURES / key).rglob("*")) if p.is_file()]
        assert bodies, f"{key} 의 녹화 픽스처가 없다 -- 이 테스트가 그 소스에 대해 아무것도 재지 못한다"
        out[key] = _datasets_that_write_bodies(cls(), bodies)
    return out


def test_every_registered_source_was_actually_replayed(replayed: dict[str, tuple[set[Dataset], int]]):
    # If every fixture ends in an exception the set above is empty, and an empty declaration
    # passes silently.
    assert set(replayed) == set(SOURCES)
    for key, (_, parsed) in replayed.items():
        assert parsed > 0, f"{key}: 성립한 파싱이 하나도 없다"


@pytest.mark.parametrize("key", sorted(SOURCES))
def test_the_declaration_matches_what_parse_actually_emits(
    replayed: dict[str, tuple[set[Dataset], int]], key: str
):
    observed, _ = replayed[key]
    declared = set(SOURCES[key].review_body_datasets)
    assert observed == declared, (
        f"{key}: parse() 가 리뷰 본문을 내는 dataset 은 {sorted(d.value for d in observed)} 인데 "
        f"선언은 {sorted(d.value for d in declared)} 다. 선언을 고치고 "
        "db/views/collection_lineage.sql 의 목록도 같이 옮겨라(needs 의 계보가 그것으로 run 을 찾는다)."
    )


def test_glowpick_writes_review_bodies_on_its_ranking_run(replayed: dict[str, tuple[set[Dataset], int]]):
    """This one line is where slopindustries/cosmai#144 got burned -- pinned separately because it is
    a fact that must not be lost.

    glowpick's `parse()` only splits off NEW_PRODUCT and no longer looks at `payload.fetch.dataset`
    (glowpick.py). The class comment writes the reason: ranking and review are **the same category
    page**.
    """
    observed, _ = replayed["glowpick"]
    assert Dataset.RANKING in observed
    # The other side: on a site that has a gate, a ranking run does not produce review bodies.
    assert Dataset.RANKING not in replayed["oliveyoung"][0]
    assert Dataset.RANKING not in replayed["daisomall"][0]


def test_a_source_cannot_be_registered_without_deciding():
    """Where it cries when a new site is added -- registration happens at import time, so the whole
    suite dies right there."""
    from collectors.commerce.registry import register

    with pytest.raises(TypeError, match="review_body_datasets"):

        @register
        class Forgetful:
            key = "forgetful"
            policy = None
            datasets = frozenset({Dataset.RANKING})

            def seeds(self, dataset, *, board=None): ...
            def parse(self, payload): ...
