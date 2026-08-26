"""`Source.review_body_datasets` 는 `parse()` 가 실제로 하는 일과 같아야 한다.

이 선언이 왜 있는가: `trend_radar.review` 에는 `run_id` 가 없다. needs 의 `collection_lineage`
(slopindustries/cosmai#144)는 리뷰 한 줄에서 그것을 걷은 run 으로 `(captured_at, sources, datasets)`
셋으로만 건너가고, 그중 `datasets` 를 틀리게 읽으면 **진짜 단일 매치가 조용히 '미상' 으로
오분류된다**. 그것이 실제로 났다: dataset 을 사이트와 무관하게 `{review, review_low}` 로 좁혔더니
glowpick 리뷰 3,597건 중 2,284건(63.5퍼센트)이 미상이 됐다 -- glowpick 의 `parse()` 는 dataset 으로
게이트하지 않아 매시 ranking 런이 리뷰 본문을 쓰기 때문이다.

그래서 선언을 SQL 이 아니라 소스 옆에 두고, 이 파일이 **선언과 코드가 갈리면 운다**. 여기서 재는
것은 문서가 아니라 동작이다: 녹화된 픽스처를 그대로 `parse()` 에 흘려 `ReviewRecord` 가 나오는
dataset 을 모으고 선언과 맞춰 본다. 게이트가 바뀌면(또는 사라지면) 그 자리가 여기서 빨개진다.

뷰가 이 선언을 비추는지는 `tests/test_collection_lineage_view.py` 가 따로 지킨다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

import collectors.commerce.sources  # noqa: F401  -- 등록이 import 부작용이다
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
    """(그 파싱이 낸 ReviewRecord 수, 파싱이 성립했는가).

    엉뚱한 dataset 의 픽스처를 먹이면 파서가 던질 수 있다 -- 그것은 이 테스트의 대상이 아니라
    조합이 무의미하다는 뜻이다. 다만 전부 던져서 조용히 초록이 되는 것은 막아야 하므로, 성립한
    파싱의 수를 아래에서 따로 센다.
    """
    try:
        out = source.parse(_payload(fetch, body))
    except Exception:  # noqa: BLE001 -- 위 docstring 의 이유. 성립 여부는 반환값이 나른다.
        return 0, False
    return len([r for r in out.records if isinstance(r, ReviewRecord)]), True


def _datasets_that_write_bodies(source: Source, bodies: list[bytes]) -> tuple[set[Dataset], int]:
    """그 소스가 리뷰 본문을 내는 dataset 들과, 성립한 파싱의 수.

    씨드 한 단계와 그 씨드가 낸 follow 한 단계를 다 본다 -- oliveyoung·daisomall 의 리뷰 본문은
    씨드(랭킹 페이지)가 아니라 그 뒤의 리뷰 엔드포인트에서 나오기 때문이다. 어느 dataset 에
    적어 두는가는 **run 의 dataset**, 즉 `seeds(...)` 를 부른 그 값이다 -- `trend_radar.run.datasets`
    가 기록하는 것이 그것이다(collectors/commerce/cli.py 의 `log.start`).
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
    # 픽스처가 전부 예외로 끝나면 위 집합이 비고 선언이 빈 소스는 조용히 통과한다.
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
    """이 한 줄이 slopindustries/cosmai#144 가 데인 자리다 -- 잃어버리면 안 되는 사실이라 따로 못박는다.

    glowpick 의 `parse()` 는 NEW_PRODUCT 만 갈라내고 `payload.fetch.dataset` 을 더는 보지 않는다
    (glowpick.py). 클래스 주석이 이유를 적는다: ranking 과 review 가 **같은 카테고리 페이지**다.
    """
    observed, _ = replayed["glowpick"]
    assert Dataset.RANKING in observed
    # 반대편: 게이트가 있는 사이트에서는 ranking 런이 리뷰 본문을 내지 않는다.
    assert Dataset.RANKING not in replayed["oliveyoung"][0]
    assert Dataset.RANKING not in replayed["daisomall"][0]


def test_a_source_cannot_be_registered_without_deciding():
    """새 사이트가 늘 때 우는 자리 -- 등록이 import 시점이라 스위트 전체가 그때 죽는다."""
    from collectors.commerce.registry import register

    with pytest.raises(TypeError, match="review_body_datasets"):

        @register
        class Forgetful:
            key = "forgetful"
            policy = None
            datasets = frozenset({Dataset.RANKING})

            def seeds(self, dataset, *, board=None): ...
            def parse(self, payload): ...
