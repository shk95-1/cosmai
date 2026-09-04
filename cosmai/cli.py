"""`cosmai` -- the one console entrypoint (contracts/entrypoints.md). `python -m cosmai.cli` so
`--help` is checkable offline in a subprocess without an installed console script (playbook
snippets/test_stack_commands_resolve.py).

`collect commerce`/`youtube`/`naver` are all wired (#7, #8, #9); naver has no live transport yet
so it ends blocked (exit 2) until #10's cutover -- declared here anyway so this module is the one
place stack/crontab and stack/docker-compose.yml can be checked against, per
contracts/entrypoints.md's collector list.

`login` (#27) is the one place a person clears a browser source's challenge by hand -- run on the
HOST from the repo root (not inside a container -- WSL2 needs no display forwarding that way), so
its profile directory is the one collector-commerce's bind mount reads.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from analysis.registry import TASKS

if TYPE_CHECKING:  # importing psycopg here drags the driver in on a single --help.
    import psycopg

STAGES = ("link", "polarity", "aggregate", "all")
# The same value as analysis.retrieval.corpus.SOURCES. It is written out again here so that a single
# `--help` does not drag psycopg in, and tests/retrieval checks that the two are the same.
RETRIEVAL_SOURCES = ("youtube_comment", "youtube_video", "youtube_transcript", "commerce_review", "mfds")
# bm25 is letters, vector is meaning, hybrid is the two rankings fused with RRF.
RETRIEVAL_ENGINES = ("bm25", "vector", "hybrid")
# analysis.retrieval.ask.DEFAULT_MODEL restated for the same reason RETRIEVAL_SOURCES is: the
# default belongs in `--help`, and importing that module would pull psycopg into every --help.
RETRIEVAL_ASK_MODEL = "claude-sonnet-5"
SPLITS = ("tune", "holdout")
KINDS = ("brand", "format", "attribute", "ingredient", "stopword", "alias", "aspect")


def _add_collect(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("collect", help="Run one collector's dataset for one hour.")
    p.add_argument("collector", choices=["commerce", "youtube", "naver"])
    p.add_argument("--dataset", required=True, help="Which dataset to collect.")
    p.add_argument("--board", default=None, help="commerce review_low only: which board to walk.")
    p.add_argument("--since", default=None, help="Accepted for shape; unused by every dataset today.")


def _add_login(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "login", help="Open a visible browser so a person can authorise one source's profile."
    )
    p.add_argument("--source", required=True, help="Source key whose browser profile to authorise.")


def _add_analyze(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("analyze", help="Run one analysis stage over the needs schema.")
    p.add_argument("stage", choices=STAGES)
    p.add_argument("--since", default=None, help="Only units observed on or after this date.")
    p.add_argument("--scope", default=None, help="Restrict to one lexicon or source category.")
    p.add_argument("--impl", default=None, help="Registered polarity factory, e.g. ollama:gemma4:latest.")
    p.add_argument(
        "--missing",
        action="store_true",
        help="Owner runs only: judge the source rows that have no row of this version yet.",
    )
    p.add_argument(
        "--url", default=None, help="SQLAlchemy URL; default is needs_runtime from the secret file."
    )


def _add_retrieval(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("retrieval", help="Build the search corpus and query it.")
    actions = p.add_subparsers(dest="action", required=True)

    chunk = actions.add_parser("chunk", help="Load needs.retrieval_chunk from the source schemas.")
    chunk.add_argument("--since", default=None, help="Only source rows observed on or after this date.")
    chunk.add_argument(
        "--source", action="append", default=None, choices=RETRIEVAL_SOURCES, help="Repeatable."
    )
    chunk.add_argument("--url", default=None, help="SQLAlchemy URL; default is needs_runtime.")

    search = actions.add_parser("search", help="Rank chunks against a query.")
    search.add_argument("--query", required=True, help="What to search for.")
    search.add_argument("--engine", default="bm25", choices=RETRIEVAL_ENGINES)
    search.add_argument(
        "--source", action="append", default=None, choices=RETRIEVAL_SOURCES, help="Repeatable."
    )
    search.add_argument("--top", type=int, default=10, help="How many chunks to print.")
    search.add_argument("--vectors", default=None, help="Vector store path; default var/retrieval/...")
    search.add_argument("--url", default=None, help="SQLAlchemy URL; default is needs_runtime.")

    ask = actions.add_parser("ask", help="Let an LLM summarise the ranked chunks in three sections.")
    ask.add_argument("--query", required=True, help="What to ask.")
    ask.add_argument("--engine", default="bm25", choices=RETRIEVAL_ENGINES)
    ask.add_argument("--source", action="append", default=None, choices=RETRIEVAL_SOURCES, help="Repeatable.")
    ask.add_argument("--top", type=int, default=10, help="How many chunks to fold into evidence.")
    ask.add_argument("--model", default=RETRIEVAL_ASK_MODEL, help="A model priced in analysis/polarity.")
    ask.add_argument(
        "--dry-run", action="store_true", help="Print the prompt and the evidence; call nothing."
    )
    ask.add_argument("--vectors", default=None, help="Vector store path; default var/retrieval/...")
    ask.add_argument("--url", default=None, help="SQLAlchemy URL; default is needs_runtime.")

    ev = actions.add_parser("eval", help="Score the retriever against the topic dictionary.")
    ev.add_argument("--mode", required=True, choices=["literal", "heldout"])
    ev.add_argument("--engine", default="bm25", choices=RETRIEVAL_ENGINES)
    ev.add_argument("--source", action="append", default=None, choices=RETRIEVAL_SOURCES, help="Repeatable.")
    ev.add_argument("--out", default=None, help="Write one row per query to this CSV.")
    ev.add_argument("--vectors", default=None, help="Vector store path; default var/retrieval/...")
    ev.add_argument("--url", default=None, help="SQLAlchemy URL; default is needs_runtime.")

    tm = actions.add_parser("terms", help="Show the frequent words the topic dictionary misses.")
    tm.add_argument("--source", action="append", default=None, choices=RETRIEVAL_SOURCES, help="Repeatable.")
    tm.add_argument("--top", type=int, default=40, help="How many unmatched terms to print.")
    tm.add_argument("--url", default=None, help="SQLAlchemy URL; default is needs_runtime.")

    emb = actions.add_parser("embed", help="Encode chunks into a vector store on disk.")
    emb.add_argument("--model", default=None, help="Sentence-transformers model; default is e5-base.")
    emb.add_argument("--device", default=None, help="cuda, cpu, ...; default is what torch picks.")
    emb.add_argument("--batch", type=int, default=256, help="Texts per forward pass.")
    emb.add_argument("--vectors", default=None, help="Vector store path; default var/retrieval/...")
    emb.add_argument("--url", default=None, help="SQLAlchemy URL; default is needs_runtime.")


def _add_trend(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("trend", help="Build the panel's quarterly topic series.")
    actions = p.add_subparsers(dest="action", required=True)

    quarter = actions.add_parser(
        "quarter", help="Load needs.metrics_topic_quarter from the active corpus snapshot."
    )
    # Neither the snapshot nor the roster is an argument -- the active version is the answer, and two
    # ways of picking it make two denominators.
    quarter.add_argument("--url", default=None, help="SQLAlchemy URL; default is needs_runtime.")

    # The verdict does not recount the metrics -- it reads the stored rows of the same run and attaches
    # the type and the two scores.
    judged = actions.add_parser(
        "judge", help="Load needs.topic_quarter_judgement from that run's quarterly rows."
    )
    judged.add_argument("--url", default=None, help="SQLAlchemy URL; default is needs_runtime.")

    # Sensitivity writes nothing -- a counterfactual population has no place in 022's vocabulary or in a run.
    wobble = actions.add_parser(
        "sensitivity", help="Ask whether the panel, the cutoff or the ad flags move that run's verdicts."
    )
    wobble.add_argument("--url", default=None, help="SQLAlchemy URL; default is needs_runtime.")

    # Evidence is a pointer attached to a judged cell -- it copies no body, so it has neither an
    # argument nor a snapshot.
    evidence = actions.add_parser(
        "evidence", help="Load needs.topic_quarter_evidence for that run's judged cells."
    )
    evidence.add_argument("--url", default=None, help="SQLAlchemy URL; default is needs_runtime.")

    # Crosscheck writes nothing either -- 022's eight columns do not fit a row keyed by one (topic) or
    # one (ingredient).
    cross = actions.add_parser(
        "crosscheck", help="Put the four sources side by side and name where they disagree."
    )
    cross.add_argument("--url", default=None, help="SQLAlchemy URL; default is needs_runtime.")

    # Holdout writes nothing either -- the boundary splitting the two arms is the chunk index, so it
    # moves on every run.
    hold = actions.add_parser(
        "holdout", help="Ask whether never-seen commerce reviews reproduce the existing ratios."
    )
    hold.add_argument("--url", default=None, help="SQLAlchemy URL; default is needs_runtime.")

    # Cards write nothing. They read the three stored tables and put markdown on stdout.
    cards = actions.add_parser("cards", help="Render the quarter's opportunity cards to stdout.")
    cards.add_argument(
        "--quarter", required=True, help="e.g. 2026Q2. A card answers a quarter-level question."
    )
    cards.add_argument("--url", default=None, help="SQLAlchemy URL; default is needs_runtime.")


def _add_eval(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("eval", help="Score one task against needs.labeled_set.")
    p.add_argument("task", choices=TASKS)
    p.add_argument("--check-baseline", action="store_true", help="Exit 1 when a baseline is missed.")
    p.add_argument("--impl", default=None, help="Registered factory to use, e.g. llm:claude-sonnet-5.")
    p.add_argument("--split", default=None, choices=SPLITS, help="Only score eval sets of this split.")
    p.add_argument(
        "--url", default=None, help="SQLAlchemy URL; default is needs_runtime from the secret file."
    )


def _add_lexicon(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("lexicon", help="Load, compare and activate a dictionary version.")
    actions = p.add_subparsers(dest="action", required=True)
    for name, helptext in (
        ("load", "Load one CSV as version n; a version already loaded is a no-op."),
        ("diff", "Show what version n adds, drops and rewrites against another version."),
        ("activate", "Make version n the active one and deactivate the rest of that kind."),
    ):
        sub = actions.add_parser(name, help=helptext)
        sub.add_argument("--kind", required=True, choices=KINDS, help="Which dictionary.")
        # diff does not require --version because one side may be a load-source CSV (fork #62).
        sub.add_argument(
            "--version",
            required=name != "diff",
            type=int,
            default=None,
            help="The dictionary version to act on.",
        )
        sub.add_argument("--url", default=None, help="SQLAlchemy URL; default is needs_runtime.")
        if name == "load":
            sub.add_argument("csv", help="CSV in the shape contracts/formats.md gives for this kind.")
        if name == "diff":
            sub.add_argument(
                "--against", type=int, default=None, help="Version to compare with (default: active)."
            )
            sub.add_argument(
                "--csv", default=None, help="Compare this load CSV with a version instead of two versions."
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cosmai")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_collect(subparsers)
    _add_login(subparsers)
    _add_analyze(subparsers)
    _add_retrieval(subparsers)
    _add_trend(subparsers)
    _add_eval(subparsers)
    _add_lexicon(subparsers)
    return parser


def _run_collect(args: argparse.Namespace) -> int:
    if args.collector == "commerce":
        from collectors.commerce.cli import run

        return run(args.dataset, board=args.board, since=args.since)
    if args.collector == "youtube":
        from collectors.youtube.cli import run

        return run(args.dataset, board=args.board, since=args.since)
    if args.collector == "naver":
        from collectors.naver.cli import run

        return run(args.dataset, board=args.board, since=args.since)
    raise AssertionError(f"unreachable: argparse choices are exhausted, got {args.collector!r}")


def _run_login(args: argparse.Namespace) -> int:
    # commerce is the only collector with a browser-transport source today (contract.py's
    # `Transport.BROWSER`, declared by oliveyoung alone) -- a `--collector` switch would be a knob
    # with one live setting. Add it back the day a second collector needs a profile of its own.
    from collectors.commerce.cli import login

    return login(args.source)


def _run_analyze(args: argparse.Namespace) -> int:
    import contextlib

    import psycopg

    from analysis import predictors, registry
    from analysis.pipeline import run_stage
    from analysis.polarity.ownership import OWNERS, unready

    if args.impl:
        registry.load_implementations()
        # eval 의 --split 강제에 대응하는 자리. analyze 에는 split 이 없고 전량이 기본이라, 유료 구현은
        # --scope 로 한 카테고리를 이름 붙여야 돈다 (재개 5번이 정확히 `--scope 선블록` 이다).
        if args.scope is None and registry.is_paid("polarity", args.impl):
            print(f"--impl {args.impl} spends money; name the corpus with --scope <category>")
            return 2
    with contextlib.ExitStack() as stack:
        try:
            since = date.fromisoformat(args.since) if args.since else None
            # The judge's dictionary and ledger connections must follow --url too, so one run does not
            # straddle two DBs (same as eval).
            predictors.set_lexicon_url(args.url)
            polarity = (
                stack.enter_context(registry.open_classifier("polarity", args.impl)) if args.impl else None
            )
            # An implementation that is not the rules runs only where the ownership table wrote its own
            # name — it is a command a person types by hand, so this is the only place that knows the
            # order (register → pass) (analysis/polarity/ownership.py).
            if polarity is not None and (blocked := unready(OWNERS, polarity.version, args.scope)):
                print(blocked)
                return 2
            conn = stack.enter_context(_connect(args.url))
        # A refusal that has not started any stage yet is blocked — the exit code tells it from a failed run.
        except (ValueError, LookupError, psycopg.Error) as refused:
            print(refused)
            return 2
        outcome = run_stage(
            conn, args.stage, since=since, scope=args.scope, missing=args.missing, polarity=polarity
        )
    print(outcome.note)
    return 0 if outcome.status == "ok" else 1


def _run_retrieval(args: argparse.Namespace) -> int:
    import psycopg

    from analysis.retrieval import corpus, pipeline
    from analysis.retrieval.topics import NoDictionary
    from analysis.retrieval.vectors import StoreMissing

    try:
        since = date.fromisoformat(args.since) if getattr(args, "since", None) else None
        # Unpack the arguments first -- there is no reason to reach the DB in order to say an argument
        # is wrong, and unpacking after the connection makes a subcommand that lacks one option die with
        # AttributeError only once it has connected.
        # embed has no --source: the options differ per subcommand, so the default is used when absent.
        sources = tuple(args.source) if getattr(args, "source", None) else corpus.SOURCES
        store = Path(args.vectors) if getattr(args, "vectors", None) else None
        conn = _connect(args.url)
    except (ValueError, LookupError, psycopg.Error) as refused:
        print(refused)
        return 2
    try:
        with conn:
            if args.action == "chunk":
                outcome = pipeline.run(conn, since=since, sources=sources)
                print(outcome.note)
                for problem in outcome.problems[:10]:
                    print(f"  {problem}")
                # A contract violation does not stop the load, but it must not pass in silence either.
                return 0 if not outcome.problems else 1
            if args.action == "eval":
                return _run_retrieval_eval(conn, args, sources, store)
            if args.action == "embed":
                return _run_retrieval_embed(conn, args, store)
            if args.action == "terms":
                return _run_retrieval_terms(conn, args, sources)
            if args.action == "ask":
                return _run_retrieval_ask(conn, args, sources, store)
            hits = pipeline.search(
                conn, args.query, engine=args.engine, top=args.top, sources=sources, store=store
            )
    # Neither a missing vector file nor a topic dictionary that is not active yet is a failure; both are
    # blocked -- they mean `embed` and `cosmai lexicon load/activate` have not been run yet.
    except (StoreMissing, NoDictionary) as blocked:
        print(blocked)
        return 2
    if not hits:
        print("no results")
        return 1
    for chunk_id, score, text in hits:
        print(f"{score:8.4f}  {chunk_id}  {text[:120]}")
    return 0


def _run_retrieval_ask(
    conn: Any, args: argparse.Namespace, sources: tuple[str, ...], store: Path | None
) -> int:
    from analysis.retrieval import ask

    try:
        answer = ask.run(
            conn,
            args.query,
            engine=args.engine,
            top=args.top,
            sources=sources,
            store=store,
            model=args.model,
            dry_run=args.dry_run,
        )
    # A refusal goes to stderr here, unlike the other retrieval subcommands: this one's stdout is
    # the markdown artefact itself (the `trend cards` convention), so one refused line redirected
    # into a `.md` would sit inside the answer.
    except ask.BLOCKING as blocked:
        print(blocked, file=sys.stderr)
        return 2
    ask.report(answer)
    print(answer.text)
    # Evidence 0 is not a failure; it is the absence of an answer -- the same place `search` puts
    # its "no results" (exit 1), and the fixed refusal is already on stdout.
    return 0 if answer.status == "ok" else 1


def _run_retrieval_terms(conn: Any, args: argparse.Namespace, sources: tuple[str, ...]) -> int:
    from analysis.retrieval import terms

    scanned = terms.scan(conn, sources=sources)
    print(terms.render(scanned, top=args.top))
    # Having seen no document at all is an empty table rather than a table -- the same place as `eval`'s
    # "0 queries scored".
    return 0 if sum(scanned.documents.values()) else 1


def _run_retrieval_embed(conn: Any, args: argparse.Namespace, store: Path | None) -> int:
    from analysis.retrieval import embed, vectors

    outcome = embed.run(
        conn,
        out=store or vectors.DEFAULT_STORE,
        model=args.model or vectors.MODEL,
        device=args.device,
        batch=args.batch,
    )
    print(outcome.note)
    return 0


def _run_retrieval_eval(
    conn: Any, args: argparse.Namespace, sources: tuple[str, ...], store: Path | None
) -> int:
    import csv

    from analysis.retrieval import eval as retrieval_eval

    rows = retrieval_eval.run(conn, args.mode, engine=args.engine, sources=sources, store=store)
    print(retrieval_eval.summary(rows))
    if args.out:
        with Path(args.out).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=retrieval_eval.FIELDS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(vars(row) for row in rows)
        print(f"saved {args.out}")
    # If not one query is scored, the chunks are empty or the dictionary is not loaded. Do not hand back
    # a silent 0.
    return 0 if rows else 1


def _connect(url: str | None) -> psycopg.Connection[Any]:
    from db.runtime import runtime_url
    from db.seed._common import connect

    return connect(url or runtime_url())


def _run_trend(args: argparse.Namespace) -> int:
    import psycopg

    from analysis.cards.pipeline import collect as cards_collect
    from analysis.cards.pipeline import report as cards_report
    from analysis.crosscheck.pipeline import NoCrosscheck
    from analysis.crosscheck.pipeline import Outcome as CrossOutcome
    from analysis.crosscheck.pipeline import run as crosscheck_run
    from analysis.evidence.pipeline import NoEvidence
    from analysis.evidence.pipeline import run as evidence_run
    from analysis.holdout.pipeline import NoHoldout
    from analysis.holdout.pipeline import Outcome as HoldOutcome
    from analysis.holdout.pipeline import run as holdout_run
    from analysis.judge.pipeline import NoJudgement
    from analysis.judge.pipeline import run as judge_run
    from analysis.retrieval.topics import NoDictionary
    from analysis.sensitivity import ShortHistory
    from analysis.sensitivity.pipeline import NoBaseline, Outcome
    from analysis.sensitivity.pipeline import run as sensitivity_run
    from analysis.trend.pipeline import NoPopulation, TopicAxisDrift
    from analysis.trend.pipeline import run as quarter_run

    try:
        conn = _connect(args.url)
    except (ValueError, LookupError, psycopg.Error) as refused:
        print(refused)
        return 2
    acts = {
        "quarter": quarter_run,
        "judge": judge_run,
        "evidence": evidence_run,
        "sensitivity": sensitivity_run,
        "crosscheck": crosscheck_run,
        "holdout": holdout_run,
    }
    try:
        with conn:
            if args.action == "cards":
                # Only cards take another branch -- the product is markdown on stdout rather than a
                # table, and there is no violation line.
                made = cards_collect(conn, args.quarter)
                print(cards_report(made), end="")
                # note goes to stderr -- stdout is the markdown product itself, so a redirect leaves a
                # `trend cards run=…` line inside the `.md`.
                print(made.note, file=sys.stderr)
                for violation in made.violations:
                    print(f"  {violation}", file=sys.stderr)
                # **카드 0건은 1 이 아니다** -- 규칙이 다 돌고 나온 답이다. 1 은 규칙에 걸렸는데 근거
                # 원문이 없어 카드로 서지 못한 셀이 있을 때뿐이다 (#41 이 §민감도 에서 못 박은 자리).
                return 0 if made.status == "ok" else 1
            outcome = acts[args.action](conn)
    # Having no roster, no snapshot, no topic dictionary and no metric or judgement row yet is blocked
    # rather than a failure -- they have not been stood up, and a snapshot that diverged from the
    # dictionary (TopicAxisDrift) likewise stands under the same command once the dictionary version is
    # matched. An empty corpus with metric rows left over (ShortHistory) is the same place: there is no
    # quarter for a window to stand on.
    # `analysis/judge`'s SparseGrid·MissingValue are not here -- #41 checked whether they are reachable in
    # sensitivity's counterfactual grid as well and they still are not (`analysis.trend.rows` emits the
    # whole topic × quarter rectangle, and a series with 0 documents makes no row). The condition for
    # catching those two is what #40 wrote down: **the day another producer writes to
    # metrics_topic_quarter**.
    except (
        NoPopulation, NoJudgement, NoEvidence, NoBaseline, NoCrosscheck, NoHoldout, TopicAxisDrift,
        NoDictionary, ShortHistory,
    ) as blocked:  # fmt: skip
        print(blocked)
        return 2
    print(outcome.note)
    # The three whose answer is sentences rather than a table are `sensitivity`·`crosscheck`·`holdout`
    # -- for the other three the note and the violation lines are everything.
    # It is isinstance rather than getattr so that the type checker carries that branch.
    if isinstance(outcome, Outcome | CrossOutcome | HoldOutcome):
        for line in outcome.lines:
            print(line)
    for violation in outcome.violations:
        print(f"  {violation}")
    # If the view says something, the table stood up but its meaning differs from the contract -- do not
    # hand back a silent 0.
    return 0 if outcome.status == "ok" else 1


def _run_eval(args: argparse.Namespace) -> int:
    from analysis import predictors, registry
    from analysis.baselines import adoption_misses
    from analysis.evaluate import evaluate, record, render

    # The predictor's dictionary connection is a separate global unrelated to registry
    # (analysis/predictors.py) -- without following --url, one eval straddles two DBs (the named one and
    # production). It has to be set now, before predict is opened.
    predictors.set_lexicon_url(args.url)
    registry.load_implementations()
    try:
        impl = registry.build(args.task, args.impl) if args.impl else registry.get(args.task)
    except LookupError as refused:
        print(refused)
        return 2
    # The baseline table hands the holdout set back first (analysis/baselines.py) — calling a paid
    # implementation without --split sends the blind holdout out on the first call. It is stopped by an
    # argument, not by discipline.
    if args.impl and registry.is_paid(args.task, args.impl) and args.split is None:
        print(f"--impl {args.impl} spends money; pass --split tune or --split holdout")
        return 2
    if impl is None:
        print(
            f"no implementation registered for {args.task!r}; the unit that owns it adds its "
            "module to analysis.registry.IMPLEMENTATIONS and registers in register_implementations()"
        )
        return 2
    import psycopg

    with _connect(args.url) as conn:
        try:
            results = evaluate(conn, args.task, impl, split=args.split)
            print(render(args.task, impl.version, results))
            print(f"analysis_run {record(conn, args.task, impl.version, results)}")
        except (LookupError, psycopg.Error) as unusable:
            print(unusable)
            return 2
    if args.check_baseline:
        misses = [miss for result in results for miss in result.misses]
        try:
            # 채택 조건은 바닥이고, 교체 조건은 규칙 실측 이상이다 (interfaces.md §규칙 실측).
            misses += list(adoption_misses(args.task, {r.name: dict(r.metrics) for r in results}))
        except LookupError as unusable:
            print(unusable)
            return 2
        for miss in misses:
            print(miss)
        return 1 if misses else 0
    return 0


def _csv_rows(kind: str, path: str) -> list[tuple[object, ...]]:
    """formats.md's dictionary CSV → db.lexicon's column order. A kind column, if there is one, must
    equal --kind."""
    from db.lexicon import ASPECT_COLUMNS, ASPECT_KIND, ENTITY_COLUMNS
    from db.seed._common import boolean, opt, read_csv

    rows = read_csv(Path(path))
    if not rows:
        raise ValueError(f"{path} has no rows")
    wanted = ASPECT_COLUMNS[:-1] if kind == ASPECT_KIND else ENTITY_COLUMNS[1:]
    missing = [c for c in wanted if c not in rows[0]]
    if missing:
        raise ValueError(f"{path} is missing column(s): {', '.join(missing)}")
    mislabelled = {r["kind"] for r in rows if "kind" in r} - {kind}
    if mislabelled:
        raise ValueError(f"{path} carries kind(s) {', '.join(sorted(mislabelled))}, not {kind}")
    if kind == ASPECT_KIND:
        # A column outside the known slots goes to `extra` -- each ruleset needs different facts (the
        # topic dictionary's surface family and topic type), and putting those in a shared slot gives one
        # column a different meaning per ruleset (021).
        # An empty cell is not a value but no entry: storing it mixes "not specified" with "empty string".
        if "extra" in rows[0]:
            # It is where the spare columns gather, so a column of that name would go inside itself --
            # do not drop it in silence.
            raise ValueError(f"{path} has an 'extra' column; spare columns become extra by name")
        spare = [c for c in rows[0] if c not in ASPECT_COLUMNS]
        return [
            (
                r["aspect"],
                r["scope"],
                r["category"],
                r["pattern"],
                boolean(r["is_neutral_noun"]),
                r["ruleset"],
                int(r["priority"]),
                {c: r[c] for c in spare if r[c] != ""},
            )
            for r in rows
        ]
    return [
        (kind, r["canonical"], r["surface"], opt(r["tier"]), opt(r["source"]), opt(r["note"])) for r in rows
    ]


def _run_lexicon(args: argparse.Namespace) -> int:
    import psycopg

    from db.lexicon import (
        ASPECT_KIND,
        activate,
        active_version,
        diff,
        diff_csv,
        insert_aspects,
        insert_entities,
    )

    with _connect(args.url) as conn, conn.cursor() as cur:
        try:
            if args.action == "load":
                rows = _csv_rows(args.kind, args.csv)
                insert = insert_aspects if args.kind == ASPECT_KIND else insert_entities
                # A new version comes in switched off — activate does the swap (formats.md).
                loaded = insert(cur, rows, args.version, active=False)
                conn.commit()
                print(
                    f"{args.kind} v{args.version}: {len(rows)} in the csv, {loaded} loaded, "
                    f"{len(rows) - loaded} already there"
                )
                return 0
            if args.action == "activate":
                touched = activate(cur, args.kind, args.version)
                conn.commit()
                print(f"{args.kind} v{args.version} is now the active version ({touched} rows touched)")
                return 0
            # If both say which version it is, there are two answers. If neither says, there is nothing
            # to compare.
            if (args.csv is None) == (args.version is None):
                raise ValueError("diff needs exactly one of --version and --csv")
            against = args.against if args.against is not None else active_version(cur, args.kind)
            if against is None:
                print(f"{args.kind} has no active version to compare with; pass --against")
                return 2
            if args.csv:
                d = diff_csv(cur, args.kind, _csv_rows(args.kind, args.csv), against)
            else:
                d = diff(cur, args.kind, args.version, against)
        # A bad CSV and a CHECK violation are both blocked — a traceback becomes exit code 1 and breaks
        # the convention.
        except (OSError, ValueError, LookupError, psycopg.Error) as refused:
            print(refused)
            return 2
    print(f"{d.kind} {d.version} vs {d.against}: +{len(d.added)} -{len(d.removed)} ~{len(d.changed)}")
    for mark, keys in (("+", d.added), ("-", d.removed), ("~", d.changed)):
        for key in keys:
            print(f"{mark} {key}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "collect":
        return _run_collect(args)
    if args.command == "login":
        return _run_login(args)
    if args.command == "analyze":
        return _run_analyze(args)
    if args.command == "retrieval":
        return _run_retrieval(args)
    if args.command == "trend":
        return _run_trend(args)
    if args.command == "eval":
        return _run_eval(args)
    if args.command == "lexicon":
        return _run_lexicon(args)
    parser.error(f"unknown command {args.command!r}")  # pragma: no cover - argparse already refused this
    return 2


if __name__ == "__main__":
    sys.exit(main())
