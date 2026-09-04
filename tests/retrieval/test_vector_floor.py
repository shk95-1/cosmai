"""벡터 검색에 유사도 하한선을 두지 않는다는 결정을 지키는 자리 (포크 #48).

지킬 것이 셋이다.

**① 판정 기준은 재기 전에 정해졌다.** 이슈 #48 본문이 세 갈래를 미리 적었고(ydc `vector_threshold.py`
가 미리 적은 것 그대로), 이 파일은 그 세 갈래가 코드에서 같은 뜻인지를 본다. 결과를 보고 기준을 만드는
것이 이 측정이 막으려는 일이라, 기준이 조용히 움직이면 판정도 조용히 움직인다.

**(2) The absence of a floor is a decision, not something unimplemented.** `vectors.search` fills the top k
whatever the cosine is. The day that property changes the contract's §Vector floor has to change with it, so
it is caught here.

**(3) Is the number that decision quoted still true.** The way to measure it is `tool/measure-vector-floor`
-- but that tool needs the 1.2GB matrix and the `embed` extra, so this suite does not call it (the same place
as the six lines of §Retrieval measurements). So what is held here is **the table's shape and the constants**,
and the numbers themselves live in the contract.
"""

from __future__ import annotations

from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
INTERFACES = ROOT / "contracts" / "interfaces.md"
ENTRYPOINTS = ROOT / "contracts" / "entrypoints.md"
TOOL = ROOT / "tool" / "measure-vector-floor"
HEADER = "## 벡터 하한선"


def loaded() -> ModuleType:
    """It has no extension, so a plain import does not reach it (the same way as
    `test_query_routing.loaded`)."""
    spec = spec_from_loader("measure_vector_floor", SourceFileLoader("measure_vector_floor", str(TOOL)))
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def section() -> str:
    body = INTERFACES.read_text(encoding="utf-8")
    start = body.index(HEADER)
    return body[start : body.index("\n## ", start)]


# The shape of our measurement: the 61 real ones sit in a narrow band with the fakes settled inside it. It
# is only that, cut down to ten.
NESTED_REAL = [0.80, *[0.84] * 4, *[0.88] * 5]
NESTED_FAKE = [0.85] * 10


def test_the_three_verdicts_were_fixed_before_any_number_was_seen():
    floor = loaded()
    # Apart is separation -- the threshold can be anywhere between the two distributions, so it gives the
    # middle.
    kind, threshold = floor.verdict([0.90, 0.91], [0.80, 0.81])
    assert kind == floor.SEPARATED and 0.81 < threshold < 0.90
    # Even overlapping, a threshold that leaves 90% of the true hits is usable if it cuts half the false ones.
    kind, threshold = floor.verdict([0.70, *[0.90] * 9], [*[0.60] * 6, *[0.95] * 4])
    assert kind == floor.USABLE and threshold == 0.90
    # If that threshold cannot cut even half the false ones it is unusable -- our measurement has this shape.
    kind, threshold = floor.verdict(NESTED_REAL, NESTED_FAKE)
    assert kind == floor.UNUSABLE and threshold == 0.84
    assert floor.verdict([], [0.5])[0] == "측정 불가"


def test_a_tie_at_the_edge_is_not_separation():
    """With nobody keeping the boundary inequality, the suite stays green when it becomes `<=` -- if the
    highest fake equals the lowest real, a query at that value is indistinguishable from a real one, so it is
    not separation."""
    floor = loaded()
    kind, threshold = floor.verdict([0.90, 0.91], [0.90, 0.80])
    assert kind != floor.SEPARATED
    assert (kind, threshold) == (floor.USABLE, 0.90)


def test_blocking_every_fake_is_not_by_itself_a_reason_to_put_a_floor_in():
    """Look only at "it blocks every fake" and any value looks good -- that is where ydc put .865 from a
    sample of 6. So when a single threshold is looked at, what it cuts and what it leaves are looked at
    **together**."""
    floor = loaded()
    high = floor.at(0.86, NESTED_REAL, NESTED_FAKE)
    assert high["fake_blocked"] == high["fake"] == 10  # it blocks every fake
    assert high["real_lost"] == 5 and high["real"] == 10  # and throws away half the true hits with them
    # That value does not leave 90% of the true hits, so it is not a threshold used in the judgement.
    assert floor.verdict(NESTED_REAL, NESTED_FAKE)[1] < 0.86


def test_the_threshold_is_always_an_observed_score_that_keeps_the_promised_share():
    """Pick a number between the values and the rule for picking it becomes one more knob."""
    floor = loaded()
    threshold = floor.highest_keeping(NESTED_REAL)
    assert threshold in NESTED_REAL
    kept = sum(1 for score in NESTED_REAL if score >= threshold) / len(NESTED_REAL)
    assert kept >= floor.KEEP
    # Take a higher measured value and it cannot leave the promised share -- which is why this is the
    # **highest** threshold.
    higher = min(score for score in NESTED_REAL if score > threshold)
    assert sum(1 for score in NESTED_REAL if score >= higher) / len(NESTED_REAL) < floor.KEEP


def test_the_fake_names_are_evidence_only_while_the_corpus_lacks_them():
    """If it is there it is not a fake. Counting it in one scan is part of the contract too -- asked per name,
    a missing name is a full scan every time and there is no guarantee twelve of them fit inside
    statement_timeout."""
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
    """Catches as a property that the absence of a floor is a decision -- the day one is added this line goes
    red, and then the contract's §Vector floor has to be fixed with it."""
    numpy = pytest.importorskip("numpy")
    from analysis.retrieval import vectors

    matrix = numpy.eye(4, vectors.DIM, dtype="float32")
    store = vectors.VectorStore(matrix, [f"d:{i}#0" for i in range(4)], ["s"] * 4, {"query_prefix": "q: "})
    far = numpy.zeros(vectors.DIM, dtype="float32")
    far[vectors.DIM - 1] = 1.0  # a query at cosine 0 to all four documents
    hits = vectors.search(store, far, top=3)
    assert len(hits) == 3
    assert [round(distance, 6) for _chunk, distance in hits] == [1.0, 1.0, 1.0]


def test_the_criteria_were_written_into_the_contract_before_the_numbers():
    """With the three branches absent from the contract, the judgement lives only inside the tool, and whoever
    fixes the tool fixes the criteria with it."""
    floor = loaded()
    body = section()
    for kind in (floor.SEPARATED, floor.USABLE, floor.UNUSABLE):
        assert f"| {kind} |" in body, kind
    assert f"{int(floor.KEEP * 100)}% 이상을 남기는 문턱" in body
    assert "절반 이상 자른다" in body and floor.CUT == 0.50
    assert "결과를 보고 기준을 만들지 않는다" in body
    # The properties of the sample are part of the criteria too -- if a fake is in the corpus, that number is
    # not the number of this table.
    assert "종료 코드 1" in body


def test_the_contract_carries_the_verdict_and_the_constants_it_was_measured_with():
    """Copy only the numbers and let the constant drift, and the next person puts a value measured by another
    criterion into this table."""
    floor = loaded()
    body = section()
    assert f"**{floor.UNUSABLE}**" in body
    assert f"| 가짜 질의 (코퍼스에 없는 성분명) | {len(floor.FAKE)} |" in body
    assert f"ydc 임시값 .{str(floor.YDC_TRIAL).split('.')[1]}" in body
    # Which store the judgement stood on (#49). Without it the next remeasurement cannot say what the delta
    # is against.
    assert "vectors=381950" in body


def test_the_decision_and_the_size_of_the_overlap_are_still_written_down():
    """With only the numbers left and the decision gone, the next person just puts a floor in."""
    body = section()
    assert "하한선을 두지 않는다" in body
    assert "결과를 보고 기준을 만들지 않는다" in body
    # Without the size of the overlap written down it reads as "they barely graze", and then "let us raise the
    # threshold a little" gets said.
    assert "사분위 구간" in body
    assert "**73.8%**" in body, "ydc 임시값이 우리 코퍼스에서 무엇을 버리는지가 이 절의 절반이다"
    assert "§Retrieval measurements" in body, "measured over the same query list, so it joins up with that"


def test_the_search_section_says_the_floor_is_absent_on_purpose():
    """Absent from the front of the contract, a person using `--engine vector` never meets this decision."""
    body = ENTRYPOINTS.read_text(encoding="utf-8")
    start = body.index("## Search (")
    search = body[start : body.index("\n## ", start)]
    assert "No similarity floor on vector search" in search
    assert "§Vector floor" in search


def test_the_lexicon_axis_carries_a_stamp_like_the_store_axis(monkeypatch: pytest.MonkeyPatch):
    """A bare number is not a version (#62): rows can be added to the active version and the number
    stays. The store axis already carried `store.stamp`; the lexicon axis carried `.version` (#68).
    Both measurements of this tool feed the same contract section, so both get the same weight."""
    floor = loaded()
    from analysis.retrieval import embed, topics

    fixed = topics.Topics((), 2, "abc123")
    monkeypatch.setattr(topics, "use_active", lambda _conn: fixed)
    monkeypatch.setattr(embed, "load_encoder", lambda *_a, **_k: object())
    monkeypatch.setattr(floor, "literal_queries", lambda _dictionary: ["query"])
    monkeypatch.setattr(floor, "same_as_csv", lambda _queries: {"same": True, "csv": 1, "active": 1})
    monkeypatch.setattr(floor, "presence", lambda _conn, names: dict.fromkeys(names, 0))
    monkeypatch.setattr(floor, "top_cosine", lambda _s, _e, queries: [(q, 0.9) for q in queries])

    measured = floor.measure_cosine(None, SimpleNamespace(stamp="model=m", model="m"), "cpu")
    assert measured["dictionary"] == fixed.stamp
    assert "version=2" in measured["dictionary"] and "fingerprint=abc123" in measured["dictionary"]
    # The df measurement writes the same key from the same lexicon; a number there would be the same
    # half-version the cosine table just stopped carrying.
    source = TOOL.read_text(encoding="utf-8")
    assert "dictionary.version" not in source
    assert source.count('"dictionary": dictionary.stamp') == 2


def test_the_contract_records_the_table_version_apart_from_what_the_tool_carries():
    """The table in the contract is a record on active lexicon v2 · 61 queries, written when the tool
    carried a number. Fixing the tool does not remeasure the table (#68), so the contract has to say
    which version the recorded rows stand on and that the next remeasurement carries the stamp."""
    body = section()
    assert "v2 · 61 queries" in body
    assert "next remeasurement" in body and "fingerprint" in body


def test_a_sample_with_no_fake_left_says_so_instead_of_crashing(capsys):
    """If all twelve are in the corpus, the sample is not wholly fake. Blowing up at that point leaves this
    tool no chance to say "do not trust this output" with the exit code 1 it promised."""
    floor = loaded()
    assert floor.band_of([], low=False) is None
    measured = {
        "store": "model=m · vectors=1 · chunked_at_max=키없음",
        "dictionary": "ruleset=r · version=2 · topics=1 · aliases=2 · fingerprint=f",
        "csv_queries": {"same": True, "csv": 2, "active": 2},
        "present": dict.fromkeys(floor.FAKE, 3),
        "real": floor.band_of([("백탁", 0.90), ("톤 업", 0.80)], low=True),
        "fake": None,
        "verdict": floor.verdict([0.90, 0.80], [])[0],
    }
    floor.report_cosine(measured)
    printed = capsys.readouterr().out
    assert "측정 불가" in printed and "표본이 비었다" in printed
    assert "가짜가 아니다" in printed


def test_the_measurement_itself_survives_a_sample_that_is_not_fake(monkeypatch: pytest.MonkeyPatch):
    """The test above looks only at the report. The side that makes the numbers has to survive in the same
    place -- emitting a quantile with no sample to make it from is what this function must not do."""
    floor = loaded()
    from analysis.retrieval import embed, topics

    monkeypatch.setattr(topics, "use_active", lambda _conn: topics.Topics((), 2, "x"))
    monkeypatch.setattr(embed, "load_encoder", lambda *_a, **_k: object())
    monkeypatch.setattr(floor, "literal_queries", lambda _dictionary: ["백탁", "톤 업"])
    monkeypatch.setattr(floor, "same_as_csv", lambda _queries: {"same": True, "csv": 2, "active": 2})
    monkeypatch.setattr(floor, "presence", lambda _conn, names: dict.fromkeys(names, 3))
    monkeypatch.setattr(floor, "top_cosine", lambda _s, _e, queries: [(q, 0.9) for q in queries])

    measured = floor.measure_cosine(None, SimpleNamespace(stamp="model=m", model="m"), "cpu")
    assert measured["fake"] is None and measured["verdict"] == "측정 불가"
    assert set(measured["present"]) == set(floor.FAKE), "왜 못 믿는지가 산출 안에 있어야 한다"
    assert "overlap" not in measured and "at_ydc_trial" not in measured
