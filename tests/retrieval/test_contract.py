"""Whether 020_retrieval_chunk.sql and the code say the same thing. The same method as
tests/collectors/*/test_*_tables_match_ddl.py, but with no SQLAlchemy metadata on this side the chunk
contract (FIELDS) is matched against the DDL columns."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from analysis.retrieval import corpus, embed
from analysis.retrieval.chunks import FIELDS
from cosmai.cli import RETRIEVAL_SOURCES

ROOT = Path(__file__).resolve().parents[2]
DDL = ROOT / "contracts" / "ddl" / "needs" / "020_retrieval_chunk.sql"
ENTRYPOINTS = ROOT / "contracts" / "entrypoints.md"
INTERFACES = ROOT / "contracts" / "interfaces.md"
INDEX = ROOT / "contracts" / "README.md"
SEARCH_HEADER = "| mode | engine | 질의 | P@10 | MRR@10 | Hit@10 |"


def _search_baseline_rows() -> dict[tuple[str, str], dict[str, str]]:
    lines = INTERFACES.read_text(encoding="utf-8").splitlines()
    columns = [c.strip() for c in SEARCH_HEADER.strip("|").split("|")]
    rows: dict[tuple[str, str], dict[str, str]] = {}
    for line in lines[lines.index(SEARCH_HEADER) + 2 :]:
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip("|").split("|")]
        rows[(cells[0], cells[1])] = dict(zip(columns, cells, strict=True))
    return rows


def _search_section() -> list[str]:
    lines = ENTRYPOINTS.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("## 검색"))
    end = next(i for i, line in enumerate(lines[start + 1 :], start + 1) if line.startswith("## "))
    return lines[start:end]


def _exit_code_bullet() -> str:
    section = _search_section()
    at = next(i for i, line in enumerate(section) if line.startswith("- 종료 코드:"))
    bullet = [section[at]]
    for line in section[at + 1 :]:
        if not line.startswith("  "):  # continuation lines only; the next item starts with `- ` again
            break
        bullet.append(line)
    return "\n".join(bullet)


def test_the_cli_source_list_matches_the_corpus_adapter():
    # cli.py writes the values out again to avoid pulling in psycopg. Drift and --source gives an empty result
    # quietly.
    assert RETRIEVAL_SOURCES == corpus.SOURCES


def test_the_exit_code_contract_covers_every_retrieval_subcommand():
    """For a subcommand the contract does not cover, the exit code exists only in the implementation -- that
    was the case for `eval`'s "0 queries -> 1" (cli.py) and `embed`'s "always 0" (#17 S6)."""
    bullet = _exit_code_bullet()
    for action in ("chunk", "search", "eval", "embed", "terms", "ask"):
        assert f"`{action}`" in bullet, action


def test_the_index_axis_sentence_credits_the_tokenizer_and_idf_not_lift():
    """fork #59: the contract once said lift removes general terms on the index axis. lift runs only in the
    `terms` report and never touches BM25 scoring; what drops those words is the tokenizer (13) and idf (16),
    the contrast tests/retrieval/test_query_stopwords.py counts. The sentence must say that, and must not
    contradict the query-axis sentence, which says neither lift nor idf is the ground there."""
    section = "\n".join(_search_section())
    assert "never touches" in section and "BM25 scoring" in section
    assert "13" in section and "16" in section and "idf" in section
    assert "lift removes" not in section and "removed by the lift" not in section


def test_the_contract_does_not_claim_which_lexicon_version_is_active():
    """fork #63: a version name in prose goes stale (it said v2 while v3 was active). The active version is
    read from the DB at run time and printed by every run; the contract may record a dated measurement but
    must not assert the current state."""
    section = "\n".join(_search_section())
    assert "is not a sentence in this file" in section
    assert "tool/show-lexicon-stamp" in section
    for stale in ("is active in production", "v2 is active", "not loaded v3"):
        assert stale not in section, stale


def test_the_search_baseline_table_carries_every_mode_and_engine():
    """The vector P@10 of heldout is the ground for adopting vectors in #28 step 4, and the only place those
    six lines lived was a review document that will be deleted -- the contract is their home (#17 S10)."""
    from analysis.retrieval import eval as retrieval_eval

    rows = _search_baseline_rows()
    assert set(rows) == {(m, e) for m in retrieval_eval.MODES for e in retrieval_eval.ENGINES}


def test_the_adoption_threshold_is_the_bm25_heldout_floor():
    # In heldout, lexical search is structurally 0. That 0 is the line the vectors have to beat, so if the
    # two drift the adoption decision loses its ground.
    rows = _search_baseline_rows()
    floor = float(rows[("heldout", "bm25")]["P@10"])
    assert floor == 0.0
    assert float(rows[("heldout", "vector")]["P@10"]) > floor
    assert "P@10 > .000" in INTERFACES.read_text(encoding="utf-8")


def test_the_ddl_lives_in_this_branchs_number_block():
    # While main keeps using 00N, a filename collision splits the apply order of db/migrate.sh.
    assert DDL.name.startswith("020_")


def test_the_contracts_index_carries_a_row_for_the_chunk_ddl():
    """The table in `contracts/README.md` is the index of the contract files and the third column is "what
    checks this". 020 was not in that table, so reading only the index made this unit's contract look absent
    (#18 M18). The additive-only check is not kept here -- tests/test_ddl_additive_only.py sweeps every file
    of this directory with a wider vocabulary, so a copy here would read as a pass stamp from the narrow
    side."""
    rows = [line for line in INDEX.read_text(encoding="utf-8").splitlines() if DDL.name in line]
    assert len(rows) == 1, rows
    checkers = [c.strip("`") for c in re.findall(r"`[^`]+`", rows[0]) if c.strip("`").endswith(".py")]
    assert checkers, rows[0]
    for checker in checkers:
        assert (ROOT / checker).exists(), checker


@pytest.mark.postgres
def test_the_table_carries_the_five_contract_columns(needs_schema: str, _schema_name: str):
    engine = create_engine(needs_schema)
    try:
        columns = {c["name"]: c for c in inspect(engine).get_columns("retrieval_chunk", schema=_schema_name)}
    finally:
        engine.dispose()
    assert set(FIELDS) <= set(columns)
    # Two derived values. Without text_md5 a rerun UPDATEs 300k rows every time.
    assert {"text_md5", "chunked_at"} <= set(columns)
    for name in FIELDS:
        assert columns[name]["nullable"] is False, name


@pytest.mark.postgres
def test_the_runtime_role_can_write_chunks(needs_runtime_url: str):
    # needs_runtime does the loading. A missing GRANT only shows up on the first production run.
    import psycopg
    from sqlalchemy.engine import make_url

    parsed = make_url(needs_runtime_url)
    conn = psycopg.connect(
        host=parsed.host,
        port=parsed.port,
        user=parsed.username,
        password=parsed.password,
        dbname=parsed.database,
        options=parsed.query["options"],  # pyright: ignore[reportArgumentType]
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO retrieval_chunk (chunk_id, doc_id, source, ordinal, text, text_md5) "
                "VALUES ('s:1#0', 's:1', 's', 0, '백탁', 'x')"
            )
            cur.execute("SELECT count(*) FROM retrieval_chunk")
            row = cur.fetchone()
            assert row is not None and row[0] == 1
        conn.commit()
    finally:
        conn.close()


def test_the_scorecard_column_the_contract_names_is_a_column():
    """When the contract names a `store` column and FIELDS has none, the CSV carries no revision -- the same
    place as the test above that checks the exit-code item is there per subcommand (#49)."""
    from analysis.retrieval import eval as retrieval_eval

    section = "\n".join(_search_section())
    assert "CSV `store` 열" in section
    # The revision and the warning are two columns on different axes -- merged into one, the revision
    # disappears when everything is normal.
    assert {"store", "note"} <= set(retrieval_eval.FIELDS)


def test_the_scorecard_carries_the_dictionary_column_on_every_engine():
    """The dictionary revision is on a different axis from `store` -- only vector and hybrid open the store,
    but the dictionary makes both the answers and the queries, so a bm25 row stands on the dictionary too
    (#62)."""
    from analysis.retrieval import eval as retrieval_eval

    section = "\n".join(_search_section())
    assert "CSV `dictionary` 열" in section
    assert {"store", "note", "dictionary"} <= set(retrieval_eval.FIELDS)


def test_the_baseline_splits_what_it_could_retrace_from_what_it_could_not():
    """Lumping what could be retraced together with what could not into one word makes the next person read
    the number `v1` as ground the DB provides -- there is no such ground (#62, the same split #49 made on the
    vector axis)."""
    text = INTERFACES.read_text(encoding="utf-8")
    # A line break is formatting, so a sentence is not read as broken by it -- going red on one rewrap means
    # nobody fixes it.
    flat = " ".join(text.split())
    assert (
        "version=1 · topics=15 · aliases=73 · fingerprint=5a0cae76311e1408" in flat
    )  # 옛 적재 원본과 남아 있는 DB v2 가 함께 대는 값
    assert "**되짚은 것 — 사전의 내용과 지문.**" in flat
    assert "**되짚을 수 없는 것 — 번호표.**" in flat
    # Erase the fact that there is no v1 row and that number reads as ground again.
    assert "v1 행이 없다" in flat


def test_the_baseline_names_the_store_the_vector_lines_stand_on():
    """Without the revision written down, the next remeasurement cannot say which store the delta is against
    -- in ydc a delta labelled "first pass -> second pass" was really "no MFDS vectors -> second pass"
    (#49)."""
    text = INTERFACES.read_text(encoding="utf-8")
    header = next(line for line in text.splitlines() if line.startswith("## 검색 실측"))
    chunks = re.search(r"청크 ([\d,]+)", header)
    stamped = re.search(r"vectors=(\d+)", text)
    assert chunks and stamped, "표 머리의 청크 수와 저장소 판본 줄 중 하나가 없다"
    # A table measured with a store whose vectors do not cover the corpus has to say so inside the table.
    assert int(stamped.group(1)) == int(chunks.group(1).replace(",", ""))
    assert "bm25 두 줄은 그 판본 위의 값이 아니다" in text


def test_the_ledger_is_a_searched_source_but_never_an_encoded_one():
    """#77 decided a fifth source rather than a router branch, and BM25 only. The comment in embed.py
    promised the exclusion would be one line here the day such a source arrived; this is the line that
    says it stayed one line -- everything else is encoded."""
    assert corpus.MFDS in corpus.SOURCES
    assert set(embed.ENCODED_SOURCES) == set(corpus.SOURCES) - {corpus.MFDS}
