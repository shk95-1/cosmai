"""The answer layer (ydc `rag/generate.py`). An LLM summarises what `retrieval search` already found.

**The LLM makes no verdict.** Rules pick the candidates, retrieval finds the evidence, and this
layer turns that evidence into three sections. So there is no judgement in this file -- a prompt
and one call.

**It is not evidence for a verdict or a card.** The three grounds on which fork #6 refused
retrieval (population, unit, order) hold here too: this answer stands on retrieved chunks, a
different denominator from the quarterly verdict table, and the prompt makes the answer say so.

**Evidence 0 calls nothing.** Rule 3 is applied by the code before it is asked of the model, and
the grounding gate (`pipeline.search`) reaches the same place by the same path -- a query whose
name the corpus never says gets the fixed refusal, not a generated paragraph.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, LiteralString

import psycopg
from psycopg.types.json import Json

from analysis.crosscheck import PAPER_HOLD
from analysis.polarity.llm import API_KEY, ESTIMATED_TOKENS_PER_CHAR, MAX_RETRIES, usage_of
from analysis.polarity.pricing import BudgetExceeded, PurposeCap, Usage, UsageLedger, price_for
from analysis.retrieval import bm25, grounding, pipeline, topics
from analysis.retrieval.vectors import StoreMissing

DEFAULT_MODEL = "claude-sonnet-5"
PURPOSE = "retrieval_ask"
# #78/#80, user decision 2026-09-05. The per-call value is read against the reservation, which
# carries the whole MAX_TOKENS output ceiling below, not against what the call turns out to cost --
# so it is well above the $0.042 a call has actually cost. Upstream #136 moves both to a knob.
ASK_CAP = PurposeCap(per_call=Decimal("0.10"), per_day=Decimal("1.00"))
# ydc's ceiling was 1100, which was a ceiling for the answer alone. Adaptive thinking spends the
# same budget (analysis/polarity/llm.py:26-29, where a one-sentence classification is given 4096),
# so at 1100 the thinking eats the three sections and the call is billed for nothing.
MAX_TOKENS = 4096
CITATION = "[Source: {}]"
SECTIONS = ("## Core", "## Evidence summary", "## Limits")
CANNOT_ANSWER = "The provided data cannot answer this. No relevant evidence was found."
# A call that came back cut off or empty. Its half of the answer is not printed: a partial three
# sections read as a whole one, and the person redirecting stdout into a `.md` cannot tell.
NO_ANSWER = "The model returned no complete answer. The call is on the ledger; nothing is written here."
TRUNCATED = "the answer stopped at max_tokens"
NO_TEXT = "the response carried no text block"


class NoKey(RuntimeError):
    """No API key for a call that is about to go out. Blocked, not failed -- `--dry-run` still runs."""


# One name for everything that stops this command before it produces an answer, so the CLI has one
# `except`. NoDictionary is a LookupError, and so is an unpriced model (`pricing.price_for`).
BLOCKING = (StoreMissing, LookupError, BudgetExceeded, NoKey)


@dataclass(frozen=True)
class Evidence:
    """One document, not one chunk. `search` ranks chunks and the citation unit is the document
    (fork #73 item 3), so a document's chunks arrive here folded into one item."""

    doc_id: str
    source: str
    text: str
    chunks: int


@dataclass(frozen=True)
class Answer:
    """What one `ask` produced. `text` is the whole of stdout -- the three sections, the fixed
    refusal, or the dry-run dump -- because stdout is redirected into a `.md` file."""

    status: str  # ok | no_evidence | incomplete
    text: str
    prompt: str
    evidence: tuple[Evidence, ...]
    note: str
    cost: str = ""
    called: bool = False
    reason: str = ""  # why an `incomplete` call produced no artefact; stderr, next to the cost


def rules(*, paper_hold: bool = PAPER_HOLD) -> tuple[str, ...]:
    """The SYSTEM rules, ported from ydc `rag/generate.py` (fork #73 item 3).

    ydc's rules 5 (MFDS) and 7 (registration-date filter) are gone: they name a source this
    repository does not have. Rules 8 and 9 keep their principle and lose their numbers -- the
    vector figure is a record of topic lexicon v1 while production runs v3, and hit rate and base
    rate belong to the verdict population, so quoting either inside a chunk-level answer mixes axes.
    Rule 10 is read from `crosscheck.PAPER_HOLD` rather than copied, so the day that axis opens the
    prompt stops claiming it is shut.
    """
    out = [
        "1. Answer **only from the evidence given below**. Do not guess or invent a fact that is not in it.",
        f"2. Put `{CITATION.format('doc_id')}` after every factual claim.",
        f'3. When the evidence is not enough, say "{CANNOT_ANSWER}" and stop. Do not force an answer.',
        "4. When the evidence comes from several sources, **keep the sources apart instead of "
        "mixing them into one conclusion**. BM25 scores and vector cosines are different scales "
        "and cannot be compared -- never cite a score number as the reason for a ranking.",
        "5. Do not assert **causation** between an ingredient and a consumer reaction. Write only "
        '"there was a mention that ...".',
        "6. Vector search misses often, and a chunk can sit close in cosine while having nothing to "
        "do with the query (a known limit of the e5 encoder). So read the **query token chunk "
        "frequency** below first.\n"
        "   - A key noun (an ingredient name, a proper name) whose frequency is 0 **does not appear "
        "in the corpus at all**. A result that came back is a chunk that happens to lie near the "
        "query vector, not a chunk about that word.\n"
        "   - In that case read the result text yourself and judge whether it touches the core of "
        f'the query. If it does not, answer "{CANNOT_ANSWER}", and **say in the answer that a query '
        "word has a frequency of 0**.",
        "7. Do not predict the future from this data. Backtesting did not show that a rise "
        'continues, so say only "this asymmetry is here now".',
    ]
    if paper_hold:
        out.append(
            "8. Do not use papers or research trends. That axis is held shut because its search "
            "terms do not count cosmetics."
        )
    return tuple(out)


FORMAT = f"""## Answer format -- these three sections and nothing else

Do not list the evidence. The person already sees it on screen. **Your job is to say what in that
evidence matters.**

```
{SECTIONS[0]}
Two to four sentences answering the question. Numbers only when the evidence has them. End every
sentence with {CITATION.format("doc_id")}.

{SECTIONS[1]}
Three bullets at most. Do not copy the evidence; say **what it tells us**. When several items say
the same thing, fold them into "N items say the same".

{SECTIONS[2]}
One or two lines: what this answer must not be used for. When the evidence sits in one source
only, say that here. Say also that this answer stands on retrieved chunks, a different denominator
from the quarterly verdict table, so it is not evidence for a verdict or a card.
```

Do not write at length. **When the core section runs past four sentences, cut it.**"""


def system_prompt(*, paper_hold: bool = PAPER_HOLD) -> str:
    head = "You are a cosmetics trend and ingredient data assistant. Keep all of the following."
    return "\n\n".join([head, "\n".join(rules(paper_hold=paper_hold)), FORMAT])


def fold(hits: list[tuple[str, float, str]], origin: dict[str, str]) -> tuple[Evidence, ...]:
    """Chunks to documents, in rank order. `chunk_id` is `doc_id#ordinal` (`eval.to_docs`), so the
    citation unit the prompt asks for exists only after this fold -- and a long document that took
    five of the ten ranks becomes one piece of evidence rather than five."""
    order: list[str] = []
    texts: dict[str, list[str]] = {}
    sources: dict[str, str] = {}
    for chunk_id, _score, text in hits:
        doc_id = chunk_id.rsplit("#", 1)[0]
        if doc_id not in texts:
            order.append(doc_id)
            texts[doc_id] = []
            # The source comes off the chunk that actually ranked. Rebuilding a `#0` id to look it
            # up would guess an id the hit list never carried, and miss whenever ordinal 0 is absent.
            sources[doc_id] = origin.get(chunk_id, "")
        texts[doc_id].append(text)
    return tuple(
        Evidence(doc_id, sources[doc_id], " ".join(texts[doc_id]), len(texts[doc_id])) for doc_id in order
    )


def token_frequency(index: bm25.Index, query: str) -> dict[str, int]:
    """Query token -> how many chunks carry it. Rule 6 reads this table, and the log stores it.

    The unit is the chunk, not the document: `Index` stands on chunks (`pipeline.load_index`), so
    `len(postings[term])` counts chunks -- `grounding` calls the same number by the same name.

    **The token axis is not the gate's.** This is `tokenize_query`, which drops query stopwords
    (fork #46); `grounding.check` is `tokenize`, the index axis. So a stopword the gate weighed is
    absent from this table, and the two can disagree about which tokens a query had. The log column
    is the record of what the prompt showed the model, which is this one.
    """
    return {term: len(index.postings.get(term, ())) for term in dict.fromkeys(bm25.tokenize_query(query))}


def build_prompt(query: str, engine: str, evidence: tuple[Evidence, ...], frequency: dict[str, int]) -> str:
    """The user turn. **Nothing is judged here** -- it lays out the question, the folded evidence
    and the frequency table, and the rules do the rest."""
    lines = [f"Question: {query}", "", f"Engine: {engine}", ""]
    if evidence:
        lines.append(f"Evidence, {len(evidence)} documents:")
        for item in evidence:
            source = f"({item.source}) " if item.source else ""
            chunks = f" [{item.chunks} chunks]" if item.chunks > 1 else ""
            lines.append(f"  - [{item.doc_id}] {source}{item.text}{chunks}")
    else:
        # Why it is empty is not spelled out here. The gate's reason is already on stderr from
        # `pipeline.search`, and this string is stdout under --dry-run -- the artefact.
        lines.append("Evidence: none")
    if frequency:
        lines += ["", "Query token chunk frequency:"]
        for term, count in frequency.items():
            flag = "   <- not in the corpus at all" if not count else ""
            lines.append(f"  {term}: {count:,}{flag}")
    if not evidence:
        lines += ["", "Rule 3 applies: answer that you cannot answer."]
    return "\n".join(lines)


def render_dry_run(system: str, prompt: str, evidence: tuple[Evidence, ...]) -> str:
    """What `--dry-run` puts on stdout: the assembled prompt and the folded evidence. A person
    reads the evidence directly here, which is this project's default way of using the layer."""
    folded = [f"{item.doc_id}\t{item.source}\t{item.chunks} chunk(s)" for item in evidence] or ["(none)"]
    return "\n".join(["== system ==", system, "", "== prompt ==", prompt, "", "== evidence ==", *folded])


def answer_text(message: Any) -> str:
    """Every text block joined; thinking blocks skipped. `content[0]` is not the answer -- Sonnet 5
    puts a thinking block first and reading `.text` off it raises (ydc 5354ee9)."""
    return "\n".join(block.text for block in message.content if getattr(block, "type", "") == "text").strip()


def estimate(system: str, prompt: str) -> Usage:
    """The reservation's guess, in the shape `analysis/polarity/llm.py` uses. The output term is
    `MAX_TOKENS` rather than a smaller guess: a reservation under the real cost makes the shared
    hard stop fire late, and late is the failure that costs money."""
    return Usage(input_tokens=len(system + prompt) * ESTIMATED_TOKENS_PER_CHAR, output_tokens=MAX_TOKENS)


def client_for(model: str) -> Any:
    """The Anthropic client, named by `contracts/secrets.md` rather than by the SDK's env default."""
    import anthropic

    from db import secrets

    try:
        key = secrets.require([API_KEY])[API_KEY]
    # secrets.require exits the process; here the missing key is one blocked command, not the end.
    except SystemExit as missing:
        raise NoKey(str(missing)) from missing
    return anthropic.Anthropic(api_key=key, max_retries=MAX_RETRIES)


LOG_INSERT: LiteralString = """
INSERT INTO retrieval_ask_log
  (query, engine, gate_ok, token_df, doc_ids, index_fingerprint, dictionary_stamp, store_stamp,
   model, usd, answer_chars)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def log_call(conn: psycopg.Connection, row: dict[str, Any]) -> None:
    """One row per real call, committed in its own short transaction **after** the round trip --
    a transaction held open across the call would meet needs_runtime's 15 s idle-in-transaction
    timeout (the reason `UsageLedger.spent()` rolls back the moment it has read)."""
    with conn.cursor() as cur:
        cur.execute(
            LOG_INSERT,
            (
                row["query"],
                row["engine"],
                row["gate_ok"],
                Json(row["token_df"]),
                row["doc_ids"],
                row["index_fingerprint"],
                row["dictionary_stamp"],
                row["store_stamp"],
                row["model"],
                row["usd"],
                row["answer_chars"],
            ),
        )
    conn.commit()


def _open_store(store: Path | None):
    """The vector store, opened once per ask. numpy stays behind a function-level import so that
    `--help` and the bm25 path never pull it in."""
    from analysis.retrieval import vectors

    return vectors.load(store or vectors.DEFAULT_STORE)


def run(
    conn: psycopg.Connection,
    query: str,
    *,
    engine: str = "bm25",
    top: int = 10,
    sources: tuple[str, ...] | None = None,
    store: Path | None = None,
    cache_dir: Path | None = pipeline.CACHE_DIR,
    model: str = DEFAULT_MODEL,
    dry_run: bool = False,
    client: Any | None = None,
    ledger: UsageLedger | None = None,
) -> Answer:
    """Retrieve, fold, ask, log. Notes go to stderr; the caller puts `Answer.text` on stdout."""
    # Refused before the corpus is touched: an unpriced model is the caller's typo, and making a
    # person wait through an index build to hear about it teaches nothing.
    price_for(model)
    # The vector path gates on the sources the store actually carries, and `search` narrows the same
    # way -- measured once here so the index, the gate, the fingerprint and the ranking are one
    # decision rather than four (#77 review).
    grounded_in = pipeline.index_sources(engine, sources)
    # Opened once and handed to `search` below -- rule 6, the log column and the ranking all stand
    # on this one index (`eval.run` passes the same handle down for the same reason).
    index, origin = pipeline.load_index(conn, grounded_in, cache_dir=cache_dir)
    # The version axes are read here, beside the index they describe, and never re-read. An
    # `activate` landing later in the run would otherwise stamp the row with a lexicon the evidence
    # never stood on -- the property #68 pinned for eval rows.
    dictionary = topics.use_active(conn)
    # The fingerprint names the index that was opened, not the one the flags asked for: on the vector
    # path those differ, and a row claiming an index nobody built cannot be traced back.
    signature = pipeline.index_signature(conn, grounded_in)
    # The census stands on the same set as the fingerprint, or the note's chunk count describes an index
    # the fingerprint does not (chunk_census's own docstring guards that pairing).
    count, _latest = pipeline.chunk_census(conn, grounded_in)
    # load_index installed the active query stopword list, which tokenize_query reads.
    frequency = token_frequency(index, query)
    # The paid path is gated whatever the engine, because a df-0 name makes the model refuse anyway
    # (#76, row 1 of the #74 table); `search` keeps #48's rule for the unpaid path.
    gate = grounding.check(query, index)
    gate_ok = gate.ok
    # The store is opened after the gate and before `search`, which keeps `search`'s own order (a
    # host with no vector file sees StoreMissing only once the gate has passed) while opening the
    # 1.2GB matrix once. `search` prints no coverage line when it is handed a store, so the warning
    # below is the only one.
    loaded = _open_store(store) if engine != "bm25" and gate_ok else None
    stamp, coverage = (loaded.stamp, pipeline.coverage_note(conn, loaded) or "") if loaded else ("", "")
    if gate_ok:
        hits = pipeline.search(
            conn,
            query,
            engine=engine,
            top=top,
            sources=sources,
            store=store,
            cache_dir=cache_dir,
            index=index,
            vector_store=loaded,
        )
    else:
        # Said here because the corpus is no longer searched at all -- this is the line `search`
        # printed for the vector path, and it is the only reason the person gets for the refusal.
        print(gate.note, file=sys.stderr)
        hits = []
    evidence = fold(hits, origin)
    note = "note: " + " · ".join(
        part
        for part in (
            f"index={signature}",
            f"chunks={count:,}",
            f"dictionary={dictionary.stamp}",
            f"store={stamp}" if stamp else "",
            coverage,
        )
        if part
    )

    system = system_prompt()
    prompt = build_prompt(query, engine, evidence, frequency)
    if dry_run:
        # Nothing is reserved and nothing is logged: no call went out, so there is no spend and no
        # row that would say one did.
        return Answer(
            status="ok" if evidence else "no_evidence",
            text=render_dry_run(system, prompt, evidence),
            prompt=prompt,
            evidence=evidence,
            note=note,
        )
    if not evidence:
        # Rule 3, applied by the code. The model is not asked to refuse; it is not asked at all.
        return Answer(status="no_evidence", text=CANNOT_ANSWER, prompt=prompt, evidence=evidence, note=note)

    # A ledger handed in by a caller keeps whatever caps it was built with.
    spend = ledger or UsageLedger(conn, caps={PURPOSE: ASK_CAP})
    params = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": system,
        "thinking": {"type": "adaptive"},
        "messages": [{"role": "user", "content": prompt}],
    }
    # Reserved before the call, so an answer that never arrives still costs the next run its budget.
    reservation = spend.reserve(model, PURPOSE, estimate(system, prompt))
    message = (client or client_for(model)).messages.create(**params)
    # Settled and logged whatever came back: the money moved, and a paid call that produced nothing
    # is the fact most worth keeping -- it is the only evidence the ceiling is too low.
    usd = spend.settle(reservation, PURPOSE, usage_of(message.usage))
    text = answer_text(message)
    cut = TRUNCATED if str(getattr(message, "stop_reason", "") or "") == "max_tokens" else ""
    cut = cut or (NO_TEXT if not text else "")
    log_call(
        conn,
        {
            "query": query,
            "engine": engine,
            "gate_ok": gate_ok,
            "token_df": frequency,
            "doc_ids": [item.doc_id for item in evidence],
            "index_fingerprint": signature,
            "dictionary_stamp": dictionary.stamp,
            "store_stamp": stamp or None,
            "model": model,
            "usd": usd,
            "answer_chars": len(text),
        },
    )
    if cut:
        # Half of three sections reads as three sections, so the half is not printed. Exit 1 says
        # the run produced no artefact; the reason and the cost say what it cost to find out.
        return Answer(
            status="incomplete",
            text=NO_ANSWER,
            prompt=prompt,
            evidence=evidence,
            note=note,
            cost=cost_note(model, usd),
            called=True,
            reason=f"note: {cut} -- the call is logged, the answer is not written",
        )
    return Answer(
        status="ok",
        text=text,
        prompt=prompt,
        evidence=evidence,
        note=note,
        cost=cost_note(model, usd),
        called=True,
    )


def cost_note(model: str, usd: Decimal) -> str:
    return f"cost: {model} · ${usd:.4f} · purpose={PURPOSE}"


def report(answer: Answer, stream=sys.stderr) -> None:
    """The version fingerprint and the cost, on stderr. Without the fingerprint the answer is a
    falsehood -- the retrieval cron is deferred, so the index goes out stale by design (fork #73
    item 5, the same discipline as "no eval row without a version")."""
    print(answer.note, file=stream)
    if answer.cost:
        print(answer.cost, file=stream)
    if answer.reason:
        print(answer.reason, file=stream)


__all__ = [
    "ASK_CAP",
    "BLOCKING",
    "CANNOT_ANSWER",
    "NO_ANSWER",
    "DEFAULT_MODEL",
    "MAX_TOKENS",
    "PURPOSE",
    "SECTIONS",
    "Answer",
    "Evidence",
    "NoKey",
    "answer_text",
    "build_prompt",
    "fold",
    "report",
    "rules",
    "run",
    "system_prompt",
    "token_frequency",
]
