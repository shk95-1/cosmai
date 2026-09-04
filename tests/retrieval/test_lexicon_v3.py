"""Does the decision ledger of topic dictionary v3 say the same thing as the dictionary (fork #56).

When #37 discarded ydc `lexicon.json` it left 5 alias kinds as "there is nowhere for them to go" and 7
candidate kinds out of the 32 in `protected`. This issue attaches one of **listed / held / unlisted** to each
of those twelve (plus the 3 kinds #37 already rejected).
원장은 `tool/measure-lexicon-candidates` 의 `LEDGER` 이고 계약 문장은 `contracts/formats.md`
§주제 사전 v3 다. 여기서 되묻는 것은 셋이다.

1. 원장이 **등재**라 적은 표기는 적재 원본에 행으로 있고, **아닌** 표기는 행이 없다.
2. 적재 원본은 얼어붙은 v1(`frozen_topics.py`)에 **이 원장이 적은 것만** 더한 사전이다 -- 그 등식이
   깨지면 `contracts/interfaces.md` §검색 실측 여섯 줄이 어느 사전 위의 값인지 말할 수 없게 된다.
3. 미등재 근거 중 **코드로 다시 물을 수 있는 것**은 코드가 묻는다: `톤업크림` 은 `톤업` 이 이미 보고,
   `sunstick`·`케미컬` 은 레포 자신의 바닥(`terms.MIN_DOCS`) 아래이며, `올영` 은 자리(kind=brand)가
   있어도 `올리브영` 이 `tier='stop'` 이라 링커가 그 표면을 아예 안 본다.

df 자체는 여기서 못 잰다 -- 코퍼스 26만 문서는 레포에 없다(`STATE.md` §3, 읽기 전용 인계본). 그 길은
도구이고, 이 파일은 **도구가 세는 규칙**을 작은 코퍼스 위에서 되묻는다.
"""

from __future__ import annotations

import json
import sys
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from typing import Any, cast

import pytest

from analysis.lexicon import compile_lexicon
from analysis.retrieval import bm25, topics
from analysis.retrieval.terms import MIN_DOCS
from analysis.types import EntitySurface
from tests.retrieval import frozen_topics
from tests.retrieval.conftest import csv_rows, csv_topics

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tool" / "measure-lexicon-candidates"


def _tool() -> Any:
    """Opens the tool as a module -- its name has a hyphen so it cannot be imported (the same way as
    `test_query_routing.py`)."""
    spec = spec_from_loader(
        "measure_lexicon_candidates", SourceFileLoader("measure_lexicon_candidates", str(TOOL))
    )
    assert spec is not None
    module = module_from_spec(spec)
    sys.modules.setdefault("measure_lexicon_candidates", module)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


LEDGER = _tool().LEDGER
LISTED = [row for row in LEDGER if row.verdict == "등재"]
NOT_LISTED = [row for row in LEDGER if row.verdict != "등재"]


def expected_entries() -> list[dict]:
    """The frozen v1 plus what this ledger writes down. `test_topics.py` matches the load source and the DB
    version against this."""
    added: list[dict] = []
    for frozen in frozen_topics.TOPICS:
        entry = {key: list(value) if isinstance(value, list) else value for key, value in frozen.items()}
        for row in LISTED:
            if row.place == entry["topic"]:
                entry[row.kind] = [*entry[row.kind], row.term]
        added.append(entry)
    return added


def _rows_by_term() -> dict[str, tuple[Any, ...]]:
    from db.lexicon import ASPECT_COLUMNS

    pattern = ASPECT_COLUMNS.index("pattern")
    return {str(row[pattern]): row for row in csv_rows()}


# ---------- does the ledger say the same thing as the load source ----------


def test_every_listed_term_lives_as_a_row_in_the_loading_source():
    """A listing decision has to live as a line in a file -- with the decision written only in the issue, the
    moment #9 deletes the slice that surface form disappears (exactly the place #37 1c left behind)."""
    from db.lexicon import ASPECT_COLUMNS

    aspect, extra = (ASPECT_COLUMNS.index(c) for c in ("aspect", "extra"))
    rows = _rows_by_term()
    for row in LISTED:
        assert row.term in rows, f"{row.term} 이 {topics.DICTIONARY_CSV.name} 에 없다"
        csv_row = rows[row.term]
        assert csv_row[aspect] == row.place, row.term
        assert csv_row[extra]["term_kind"] == row.kind, row.term


def test_no_refused_term_slipped_into_the_loading_source():
    """Held and unlisted are decisions with grounds -- a row appearing quietly makes those grounds false."""
    rows = _rows_by_term()
    assert [row.term for row in NOT_LISTED if row.term in rows] == []


def test_the_loading_source_is_the_frozen_v1_plus_exactly_this_ledger():
    """v1 과의 차이가 **정확히 이 원장**이라는 것. 여기가 깨지면 `contracts/interfaces.md`
    §검색 실측 여섯 줄이 어느 사전 위의 값인지 말할 수 없다."""
    loaded = csv_topics()
    assert [e["topic"] for e in loaded.entries] == [e["topic"] for e in frozen_topics.TOPICS]
    for got, want in zip(loaded.entries, expected_entries(), strict=True):
        assert got["ko"] == want["ko"], got["topic"]
        assert got["latin"] == want["latin"], got["topic"]
        assert got["topic_type"] == want["topic_type"], got["topic"]
        assert got["trend_use"] == want["trend_use"], got["topic"]


def test_the_ledger_only_touches_topics_that_already_exist():
    """새 주제를 세우는 것은 별칭을 옮기는 일이 아니다 -- 보류 셋(`화잘먹`·`비비크림`)이 그 자리다."""
    known = {e["topic"] for e in frozen_topics.TOPICS} | {"brand"}
    assert [row.term for row in LEDGER if row.place not in known] == []


# ---------- does the code ask the unlisted grounds back ----------


def test_a_term_its_topic_already_sees_earns_no_row():
    """`톤업크림` 의 미등재 근거. `톤업` 이 그 문자열을 이미 부분문자열로 잡으므로 행을 더해도
    그 주제가 새로 보는 문서가 없다 -- 변이(`파데프리`)를 옆에 두어 이 물음이 공회전이 아님을 보인다."""
    assert "톤업_메이크업베이스" in frozen_topics.match_topics("톤업크림", include_excluded=True)
    assert frozen_topics.match_topics("파데프리", include_excluded=True) == []


def test_the_floor_the_ledger_refuses_on_is_the_repos_own_floor():
    """`sunstick` 3 · `케미컬` 3 을 기각한 바닥은 이 이슈가 지어낸 수가 아니라 `terms.MIN_DOCS` 다."""
    assert MIN_DOCS == 5
    below = {row.term: row.df for row in NOT_LISTED if row.df < MIN_DOCS}
    assert below == {"sunstick": 3, "케미컬": 3, "olive영": 0}
    assert all(row.df >= MIN_DOCS for row in LISTED)


def test_a_compound_that_adds_no_document_can_still_earn_its_row_on_the_token_axis(repo_dictionary):
    """`속건조` 는 신규 문서가 0 이라 매칭 축에서는 아무것도 안 바꾼다. 등재 근거는 토큰 축이다 --
    Kiwi 가 `속`+`건조` 로 쪼개던 것을 한 토큰으로 주고, 확장이 `건조` 를 그대로 지킨다."""
    tokens = bm25.tokenize("속건조가 심해요")
    assert "속건조" in tokens and "건조" in tokens


def test_the_criterion_that_separates_속건조_from_톤업크림_is_the_token_it_would_lose(repo_dictionary):
    """등재 기준 2 는 "관측되는 것이 늘되 있던 것을 잃지 않는다"이다. 둘 다 `new` 0 이고 둘 다 토큰이
    달라지므로, 판정을 가른 것은 **손실**이다 -- 별칭이 Kiwi 사용자 단어가 되어 복합어를 묶으면 조각
    토큰이 사라진다. `속건조` 는 확장이 `건조` 를 지키고, `톤업크림` 은 `크림` 을, `비비크림` 은
    `비비`·`크림` 둘 다 잃는다."""
    tool = _tool()
    wider = tool.with_terms(csv_topics(), [r for r in LEDGER if r.term in ("톤업크림", "비비크림")])
    topics.use(wider)
    assert bm25.tokenize("톤업크림 발라요") == ["톤업크림", "톤업", "바르"]  # `크림` 이 없다
    assert bm25.tokenize("비비크림 추천") == ["비비크림", "추천"]  # `비비`·`크림` 둘 다 없다


def test_a_stop_tier_canonical_keeps_every_surface_of_its_out_of_the_linker():
    """`올영` 미등재의 근거. 자리(kind=brand)는 있지만 `올리브영` 은 유통 채널이라 `tier='stop'` 이고,
    `compile_lexicon` 이 그 정본의 표면을 `surface_re` 에서 통째로 뺀다 -- 행을 더해도 링커가 그것을
    볼 일이 없다. 변이: tier 를 normal 로 돌리면 바로 잡힌다(그러면 유통 채널이 브랜드 집계에 든다)."""
    stopped = [
        EntitySurface("brand", "올리브영", "올리브영", "stop", "oliveyoung"),
        EntitySurface("brand", "올리브영", "올영", "stop", "oliveyoung"),
    ]
    assert compile_lexicon(stopped, 1).surface_re.search("올영 세일 갔어요") is None
    linkable = [EntitySurface(s.kind, s.canonical, s.surface, "normal", s.source) for s in stopped]
    assert compile_lexicon(linkable, 1).surface_re.search("올영 세일 갔어요") is not None


# ---------- the rule the tool counts by ----------


# The same column order as the handover copy. Written out by hand, the schema the tool reads and the fixture
# drift apart quietly.
CORPUS_HEADER = (ROOT / "tests" / "fixtures" / "yt_handoff" / "document.csv").read_text(
    encoding="utf-8-sig"
).splitlines()[0] + "\n"
PROBES = (
    "썬쿠션 하나 샀어요",  # 선크림이 못 보던 문서 -- 다른 주제도 안 걸린다
    "sunscreens 여러 개 비교",  # 경계 매칭이면 sunscreen 이 아니다
    "sunscreen 하나 챙겼어요",  # 경계 매칭으로 잡힌다
    "선크림 중에 썬쿠션이 제일 낫다",  # 이미 선크림이 보는 문서
    "속건조가 너무 심해요",  # 건조가 이미 보는 문서
)


def _corpus(tmp_path: Path) -> Path:
    path = tmp_path / "document.csv"
    rows = "".join(f"d{i},youtube_comment,c{i},comment,,,,,{text},,{{}}\n" for i, text in enumerate(PROBES))
    path.write_text(CORPUS_HEADER + rows, encoding="utf-8")
    return path


def test_the_tool_counts_latin_on_the_boundary_rule_the_dictionary_matches_on(tmp_path):
    """Why the `sunscreen` ledger value is 76 rather than 81. Counted as a substring the plural `sunscreens`
    slips in, but the dictionary matches on boundaries so that document is one the dictionary does not see --
    put the two numbers side by side and the axes are split."""
    tool = _tool()
    measured = tool.measure(_corpus(tmp_path), tool.baseline())
    assert measured["documents"] == len(PROBES)
    assert measured["df"]["sunscreen"] == 1  # `sunscreens` 는 안 센다
    assert measured["df"]["썬쿠션"] == 2


def test_the_tool_separates_what_a_row_adds_from_what_the_topic_already_sees(tmp_path):
    """`new` 가 `df` 와 다른 수라는 것. 같으면 `톤업크림`(628편 전부를 `톤업` 이 본다)을 기각한 근거가
    사라진다."""
    tool = _tool()
    measured = tool.measure(_corpus(tmp_path), tool.baseline())
    assert measured["new"]["썬쿠션"] == 1  # 두 문서 중 `선크림` 이 든 쪽은 이미 보인다
    assert measured["new"]["속건조"] == 0  # `건조` 가 이미 본다
    assert measured["unseen"]["썬쿠션"] == 1


def test_the_tool_refuses_when_a_listed_term_falls_through_the_floor(tmp_path):
    """Where a growing corpus makes the ledger false. Exit code 1 = do not trust this output."""
    tool = _tool()
    assert tool.main(["--corpus", str(_corpus(tmp_path)), "--json"]) == 1
    measured = tool.measure(_corpus(tmp_path), tool.baseline())
    assert [m for m in tool.misses(measured) if m.startswith("floor   파데프리")]


def _ledger_corpus(tmp_path: Path, tool: Any, repeats: dict[str, int] | None = None) -> Path:
    """A corpus that reproduces the ledger's `df` and `new` **exactly**. Per spelling, the `new` side holds
    that spelling alone (a document that topic does not see) and the `df - new` side adds the topic's first
    alias (a document it already sees). With only the red place present and green unreachable, the tests
    above are identities."""
    known = tool.baseline()
    filler = {entry["topic"]: entry["ko"][0] for entry in known.entries}
    lines = []
    for index, row in enumerate(tool.LEDGER):
        seen = "" if row.place == tool.BRAND else " " + filler[row.place]
        extra = (repeats or {}).get(row.term, 0)
        texts = [row.term] * (row.new + extra) + [row.term + seen] * (row.df - row.new)
        lines += [
            f"d{index}_{n},youtube_comment,d{index}_{n},comment,,,,,{t},,{{}}" for n, t in enumerate(texts)
        ]
    path = tmp_path / "document.csv"
    path.write_text(CORPUS_HEADER + "\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_the_tool_is_green_on_a_corpus_that_matches_the_ledger(tmp_path, capsys):
    tool = _tool()
    assert tool.main(["--corpus", str(_ledger_corpus(tmp_path, tool)), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["misses"] == []


def test_the_tool_compares_the_ledgers_own_numbers_and_not_only_the_verdicts(tmp_path, monkeypatch):
    """The reason this tool exists is to measure the ledger's numbers again and **match them**. Without the
    comparison, any number written in the ledger stays green and the df the contract quotes goes quietly
    false (every other measure-* in the repo is an exact match).

    변이: `썬쿠션 116` 을 `9116` 으로 바꾸면 그 한 줄이 `count` 로 나온다.
    """
    tool = _tool()
    corpus = _ledger_corpus(tmp_path, tool)
    assert tool.misses(tool.measure(corpus, tool.baseline())) == []
    row = next(r for r in tool.LEDGER if r.term == "썬쿠션")
    monkeypatch.setattr(row, "df", 9116)
    reported = tool.misses(tool.measure(corpus, tool.baseline()))
    assert [m for m in reported if m.startswith("count   썬쿠션") and "9116" in m], reported


def test_the_revived_guard_reaches_a_row_that_was_refused_on_the_floor(tmp_path):
    """근거가 수인 미등재는 그 근거로 되물어야 한다. 옛 판은 `row.new == 0` 인 행에만 걸어서 df 로
    기각한 `케미컬`(3·3)·`olive영`(0·0)이 구조적으로 안 걸렸다 -- 바닥을 넘으면 다시 판정할 때다."""
    tool = _tool()
    corpus = _ledger_corpus(tmp_path, tool, repeats={"케미컬": 6})
    reported = tool.misses(tool.measure(corpus, tool.baseline()))
    assert [m for m in reported if m.startswith("revived 케미컬")], reported
    assert [m for m in reported if m.startswith("count   케미컬")], reported


def test_a_held_rows_ground_is_not_numeric_so_only_the_count_guard_speaks_for_it(tmp_path):
    """`모공막힘`·`화잘먹`·`비비크림` 의 근거는 축과 표본이라 수로는 안 무너진다 -- 그 행들이 원장대로인
    코퍼스에서 빨개지면 도구가 운영 코퍼스에서 영영 종료 1 이 된다. 대신 **수가 움직이면** `count` 가
    잡는다: 그것이 사람이 축을 다시 읽어야 한다는 신호다."""
    tool = _tool()
    plain = tool.misses(tool.measure(_ledger_corpus(tmp_path, tool), tool.baseline()))
    assert plain == []
    moved = tool.misses(
        tool.measure(_ledger_corpus(tmp_path, tool, repeats={"모공막힘": 7}), tool.baseline())
    )
    assert [m for m in moved if m.startswith("count   모공막힘")], moved


def test_the_tool_counts_the_topic_totals_the_contract_quotes(tmp_path, capsys):
    """계약이 인용하는 `선크림` 12,197 -> 12,418 과 `밀림_들뜸` 959 -> 2,021 이 나오는 자리. 뒤엣것은
    등재 기준 3 이 딛고 선 유일한 수라, 재는 길이 없으면 그 문턱이 근거 없는 수가 된다."""
    tool = _tool()
    assert tool.main(["--corpus", str(_ledger_corpus(tmp_path, tool)), "--topics", "--json"]) == 0
    moved = json.loads(capsys.readouterr().out)
    assert moved["listed"]["선크림"]["after"] > moved["listed"]["선크림"]["before"]
    held = moved["held_or_refused"]["밀림_들뜸"]
    assert held["after"] - held["before"] == next(r for r in tool.LEDGER if r.term == "화잘먹").new


def test_the_tool_reads_the_repo_csv_as_its_baseline_dictionary():
    """The base dictionary holds not one of the ledger's spellings -- holding one, every `new` becomes 0
    after listing and "what does this row observe" can no longer be asked."""
    tool = _tool()
    aliases = {a for e in tool.baseline().entries for a in e["ko"] + e["latin"]}
    assert not aliases & {row.term for row in LEDGER}
    assert {a for e in csv_topics().entries for a in e["ko"] + e["latin"]} & {r.term for r in LISTED} == {
        row.term for row in LISTED
    }


def test_the_tool_is_wired_to_the_read_only_handoff_corpus():
    """With the default path inside the repo it measures the fixture rather than 260k documents, and that
    number goes into the ledger."""
    tool = _tool()
    assert tool.CORPUS.name == "document.csv"
    assert "yt-handoff" in str(tool.CORPUS)
    with pytest.raises(FileNotFoundError):
        tool.measure(Path("/nonexistent/document.csv"), tool.baseline())


# ---------- what moves across a load (the first measurement of fork #58) ----------


@pytest.mark.postgres
def test_raising_the_aspect_dictionary_to_v3_does_not_move_the_run_stamp(needs_runtime_url: str):
    """Fork #58 writes that the number of `entity_lexicon` does not pick by kind. **The topic dictionary does
    not step on that place** -- `aspect_lexicon` is a different table, so it does not share
    `versions.lexicon` (the `SELECT max(version) FROM entity_lexicon` at `aggregate/pipeline.py:149`). What
    moves and what does not when the coordinator switches v3 on is the answer of this test.

    변이: `entity_lexicon` 에 v3 를 한 행 넣으면 (활성이 아니어도) 그 칸이 3 이 된다 -- #58 이 고칠
    자리가 실제로 무는 것을 여기서 본다.
    """
    from analysis.aggregate.pipeline import _versions
    from analysis.lexicon import load_aspects
    from analysis.retrieval import topics as topic_registry
    from cosmai.cli import main as cli
    from db.lexicon import insert_entities
    from tests.retrieval.test_topics import _connect

    class _Aggregator:
        version = "rules-test"

    stamp = lambda cur: _versions(cast(Any, _Aggregator()), cur, [])["lexicon"]  # noqa: E731

    with _connect(needs_runtime_url) as conn, conn.cursor() as cur:
        insert_entities(cur, [("brand", "라네즈", "라네즈", "normal", "oliveyoung", None)], 1)
        conn.commit()
        assert stamp(cur) == 1
        for version in (1, 3):
            argv = ["lexicon", "load", "--kind", "aspect", "--version", str(version)]
            assert cli([*argv, str(topic_registry.DICTIONARY_CSV), "--url", needs_runtime_url]) == 0
        assert (
            cli(["lexicon", "activate", "--kind", "aspect", "--version", "3", "--url", needs_runtime_url])
            == 0
        )
        conn.commit()
        assert load_aspects(conn, topic_registry.RULESET).version == 3
        assert topic_registry.load(conn).version == 3
        assert stamp(cur) == 1, "aspect v3 가 entity 의 번호표를 움직였다"
        # Variant: one v3 row arriving on the entity side moves that column (even unswitched) (#58).
        insert_entities(cur, [("brand", "라네즈", "라네즈", "normal", "oliveyoung", None)], 3, active=False)
        conn.commit()
        assert stamp(cur) == 3
