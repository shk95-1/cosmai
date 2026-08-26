"""벡터 검색에 유사도 하한선을 두지 않는다는 결정을 지키는 자리 (포크 #48).

지킬 것이 셋이다.

**① 판정 기준은 재기 전에 정해졌다.** 이슈 #48 본문이 세 갈래를 미리 적었고(ydc `vector_threshold.py`
가 미리 적은 것 그대로), 이 파일은 그 세 갈래가 코드에서 같은 뜻인지를 본다. 결과를 보고 기준을 만드는
것이 이 측정이 막으려는 일이라, 기준이 조용히 움직이면 판정도 조용히 움직인다.

**② 하한선이 없다는 것은 결정이지 미구현이 아니다.** `vectors.search` 는 코사인이 얼마든 상위 k 를
채운다. 그 성질이 바뀌는 날 계약 §벡터 하한선 이 함께 바뀌어야 하므로 여기서 잡는다.

**③ 그 결정이 인용한 수가 아직 참인가.** 재는 길은 `tool/measure-vector-floor` 다 -- 다만 그 도구는
1.2GB 행렬과 `embed` extra 를 열어야 해서 이 스위트가 부르지 않는다(§검색 실측 여섯 줄과 같은 자리다).
그래서 여기서 붙드는 것은 **표의 모양과 상수**이고, 수 자체의 거처는 계약이다.
"""

from __future__ import annotations

from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
INTERFACES = ROOT / "contracts" / "interfaces.md"
ENTRYPOINTS = ROOT / "contracts" / "entrypoints.md"
TOOL = ROOT / "tool" / "measure-vector-floor"
HEADER = "## 벡터 하한선"


def loaded() -> ModuleType:
    """확장자가 없어 평범한 import 로는 안 들어온다 (`test_query_routing.loaded` 와 같은 길)."""
    spec = spec_from_loader("measure_vector_floor", SourceFileLoader("measure_vector_floor", str(TOOL)))
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def section() -> str:
    body = INTERFACES.read_text(encoding="utf-8")
    start = body.index(HEADER)
    return body[start : body.index("\n## ", start)]


# 진짜 61개가 좁은 띠에 있고 가짜가 그 안에 들어앉은 우리 실측의 모양. 열 개로 줄인 것뿐이다.
NESTED_REAL = [0.80, *[0.84] * 4, *[0.88] * 5]
NESTED_FAKE = [0.85] * 10


def test_the_three_verdicts_were_fixed_before_any_number_was_seen():
    floor = loaded()
    # 갈리면 분리다 -- 문턱은 두 분포 사이 어디든 되므로 가운데를 준다.
    kind, threshold = floor.verdict([0.90, 0.91], [0.80, 0.81])
    assert kind == floor.SEPARATED and 0.81 < threshold < 0.90
    # 겹쳐도 정탐 90% 를 남기는 문턱이 오탐 절반을 자르면 쓸 수 있다.
    kind, threshold = floor.verdict([0.70, *[0.90] * 9], [*[0.60] * 6, *[0.95] * 4])
    assert kind == floor.USABLE and threshold == 0.90
    # 그 문턱이 오탐을 절반도 못 자르면 못 쓴다 -- 우리 실측이 이 모양이다.
    kind, threshold = floor.verdict(NESTED_REAL, NESTED_FAKE)
    assert kind == floor.UNUSABLE and threshold == 0.84
    assert floor.verdict([], [0.5])[0] == "측정 불가"


def test_blocking_every_fake_is_not_by_itself_a_reason_to_put_a_floor_in():
    """`가짜를 다 막는다`만 보면 어떤 값이든 좋아 보인다 -- ydc 가 표본 6개로 .865 를 넣은 자리다.
    그래서 문턱 하나를 볼 때는 자르는 쪽과 남기는 쪽을 **함께** 본다."""
    floor = loaded()
    high = floor.at(0.86, NESTED_REAL, NESTED_FAKE)
    assert high["fake_blocked"] == high["fake"] == 10  # 가짜를 다 막는데
    assert high["real_lost"] == 5 and high["real"] == 10  # 정탐 절반을 함께 버린다
    # 그 값은 정탐 90% 를 남기지 못하므로 판정에 쓰이는 문턱이 아니다.
    assert floor.verdict(NESTED_REAL, NESTED_FAKE)[1] < 0.86


def test_the_threshold_is_always_an_observed_score_that_keeps_the_promised_share():
    """값 사이의 수를 고르면 그 고르는 규칙이 또 하나의 손잡이가 된다."""
    floor = loaded()
    threshold = floor.highest_keeping(NESTED_REAL)
    assert threshold in NESTED_REAL
    kept = sum(1 for score in NESTED_REAL if score >= threshold) / len(NESTED_REAL)
    assert kept >= floor.KEEP
    # 더 높은 실측값을 잡으면 약속한 몫을 못 남긴다 -- 그래서 이것이 **가장 높은** 문턱이다.
    higher = min(score for score in NESTED_REAL if score > threshold)
    assert sum(1 for score in NESTED_REAL if score >= higher) / len(NESTED_REAL) < floor.KEEP


def test_the_fake_names_are_evidence_only_while_the_corpus_lacks_them():
    """있으면 가짜가 아니다. 한 번의 훑기로 세는 것도 계약이다 -- 이름마다 물으면 없는 이름은 매번
    전량 훑기이고 열두 번이 statement_timeout 안에 든다는 보장이 없다."""
    floor = loaded()
    asked: list[tuple[str, tuple]] = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, sql, params):
            asked.append((sql, params))

        def fetchone(self):
            return tuple(0 for _ in floor.FAKE)

    class Conn:
        def cursor(self):
            return Cursor()

        def commit(self):
            asked.append(("commit", ()))

    found = floor.presence(Conn(), floor.FAKE)
    assert set(found) == set(floor.FAKE) and set(found.values()) == {0}
    statements = [sql for sql, _ in asked if sql != "commit"]
    assert len(statements) == 1, statements
    assert statements[0].count("%s") == len(floor.FAKE)
    assert asked[0][1] == tuple(f"%{name}%" for name in floor.FAKE)
    assert ("commit", ()) in asked, "행렬을 열기 전에 트랜잭션을 닫지 않으면 idle in transaction 이 남는다"


def test_search_fills_top_k_however_far_the_query_is():
    """하한선이 없다는 것이 결정이라는 것을 성질로 잡는다 -- 넣는 날 이 줄이 빨개지고, 그때 계약
    §벡터 하한선 을 함께 고쳐야 한다."""
    numpy = pytest.importorskip("numpy")
    from analysis.retrieval import vectors

    matrix = numpy.eye(4, vectors.DIM, dtype="float32")
    store = vectors.VectorStore(matrix, [f"d:{i}#0" for i in range(4)], ["s"] * 4, {"query_prefix": "q: "})
    far = numpy.zeros(vectors.DIM, dtype="float32")
    far[vectors.DIM - 1] = 1.0  # 네 문서 중 어느 것과도 코사인 0 인 질의
    hits = vectors.search(store, far, top=3)
    assert len(hits) == 3
    assert [round(distance, 6) for _chunk, distance in hits] == [1.0, 1.0, 1.0]


def test_the_criteria_were_written_into_the_contract_before_the_numbers():
    """세 갈래가 계약에 없으면 판정이 도구 안에서만 살고, 도구를 고치는 사람이 기준도 함께 고친다."""
    floor = loaded()
    body = section()
    for kind in (floor.SEPARATED, floor.USABLE, floor.UNUSABLE):
        assert f"| {kind} |" in body, kind
    assert f"{int(floor.KEEP * 100)}% 이상을 남기는 문턱" in body
    assert "절반 이상 자른다" in body and floor.CUT == 0.50
    assert "결과를 보고 기준을 만들지 않는다" in body
    # 표본의 성질도 기준의 일부다 -- 가짜가 코퍼스에 있으면 그 수는 이 표의 수가 아니다.
    assert "종료 코드 1" in body


def test_the_contract_carries_the_verdict_and_the_constants_it_was_measured_with():
    """수만 옮겨 적고 상수가 갈리면 다음 사람이 다른 기준으로 잰 값을 이 표에 넣는다."""
    floor = loaded()
    body = section()
    assert f"**{floor.UNUSABLE}**" in body
    assert f"| 가짜 질의 (코퍼스에 없는 성분명) | {len(floor.FAKE)} |" in body
    assert f"ydc 임시값 .{str(floor.YDC_TRIAL).split('.')[1]}" in body
    # 판정이 어느 저장소 위에 섰는지 (#49). 없으면 다음 재측정이 무엇과의 델타인지 말할 수 없다.
    assert "vectors=381950" in body


def test_the_decision_and_the_size_of_the_overlap_are_still_written_down():
    """수만 남고 결정이 사라지면 다음 사람이 하한선을 그냥 넣는다."""
    body = section()
    assert "하한선을 두지 않는다" in body
    assert "결과를 보고 기준을 만들지 않는다" in body
    # 겹침의 크기를 안 적으면 "스치듯 겹친다"로 읽히고, 그러면 문턱을 조금 올려 보자는 말이 선다.
    assert "사분위 구간" in body
    assert "**73.8%**" in body, "ydc 임시값이 우리 코퍼스에서 무엇을 버리는지가 이 절의 절반이다"
    assert "§검색 실측" in body, "같은 질의 목록으로 잰 것이라 그쪽과 이어져 있어야 한다"


def test_the_search_section_says_the_floor_is_absent_on_purpose():
    """계약의 입구 쪽에 없으면 `--engine vector` 를 쓰는 사람은 이 결정을 영영 안 만난다."""
    body = ENTRYPOINTS.read_text(encoding="utf-8")
    start = body.index("## 검색 (")
    search = body[start : body.index("\n## ", start)]
    assert "유사도 하한선을 두지 않는다" in search
    assert "§벡터 하한선" in search
