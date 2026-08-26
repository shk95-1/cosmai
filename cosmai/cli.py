"""`cosmai` -- the one console entrypoint (contracts/entrypoints.md). `python -m cosmai.cli` so
`--help` is checkable offline in a subprocess without an installed console script (playbook
snippets/test_stack_commands_resolve.py).

TODO(#95): the sentence below is stale -- naver dispatches and dies with a traceback, not exit 2.
`collect commerce`/`youtube` are wired (#7, #8); `naver` is #9 and refuses cleanly until then --
declared here anyway so this module is the one place stack/crontab and stack/docker-compose.yml can be
checked against, per contracts/entrypoints.md's collector list.

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

if TYPE_CHECKING:  # psycopg 를 여기서 import 하면 --help 한 번에도 드라이버가 딸려 온다.
    import psycopg

STAGES = ("link", "polarity", "aggregate", "all")
# analysis.retrieval.corpus.SOURCES 와 같은 값. 여기서 다시 적는 이유는 `--help` 한 번에
# psycopg 를 딸려 오게 하지 않으려는 것이고, tests/retrieval 이 둘이 같은지 검사한다.
RETRIEVAL_SOURCES = ("youtube_comment", "youtube_video", "youtube_transcript", "commerce_review")
# bm25 는 글자, vector 는 뜻, hybrid 는 둘의 순위를 RRF 로 합친 것.
RETRIEVAL_ENGINES = ("bm25", "vector", "hybrid")
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
    p.add_argument("--scope", default=None, help="Restrict to one lexicon category.")
    p.add_argument("--impl", default=None, help="Registered polarity factory, e.g. ollama:gemma4:latest.")
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
    # 스냅샷도 명부도 인자가 아니다 -- 활성 판본이 답이고, 그것을 고르는 길이 둘이면 분모도 둘이 된다.
    quarter.add_argument("--url", default=None, help="SQLAlchemy URL; default is needs_runtime.")

    # 판정은 지표를 다시 세지 않는다 -- 같은 run 의 저장된 행을 읽어 유형과 두 점수를 붙인다.
    judged = actions.add_parser(
        "judge", help="Load needs.topic_quarter_judgement from that run's quarterly rows."
    )
    judged.add_argument("--url", default=None, help="SQLAlchemy URL; default is needs_runtime.")

    # 민감도는 아무것도 쓰지 않는다 -- 반사실 모집단에는 022 의 어휘에도 run 에도 자리가 없다.
    wobble = actions.add_parser(
        "sensitivity", help="Ask whether the panel, the cutoff or the ad flags move that run's verdicts."
    )
    wobble.add_argument("--url", default=None, help="SQLAlchemy URL; default is needs_runtime.")

    # 근거는 판정 셀에 붙는 포인터다 -- 본문을 베끼지 않으므로 인자도 스냅샷도 없다.
    evidence = actions.add_parser(
        "evidence", help="Load needs.topic_quarter_evidence for that run's judged cells."
    )
    evidence.add_argument("--url", default=None, help="SQLAlchemy URL; default is needs_runtime.")

    # 대조도 아무것도 쓰지 않는다 -- (주제)나 (성분) 하나가 키인 행에는 022 의 여덟 칸이 맞지 않는다.
    cross = actions.add_parser(
        "crosscheck", help="Put the four sources side by side and name where they disagree."
    )
    cross.add_argument("--url", default=None, help="SQLAlchemy URL; default is needs_runtime.")

    # 카드는 아무것도 쓰지 않는다. 저장된 세 표를 읽어 마크다운을 stdout 으로 낸다.
    cards = actions.add_parser("cards", help="Render the quarter's opportunity cards to stdout.")
    cards.add_argument("--quarter", required=True, help="예: 2026Q2. 카드는 분기 단위의 물음이다.")
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
        sub.add_argument("--version", required=True, type=int, help="The dictionary version to act on.")
        sub.add_argument("--url", default=None, help="SQLAlchemy URL; default is needs_runtime.")
        if name == "load":
            sub.add_argument("csv", help="CSV in the shape contracts/formats.md gives for this kind.")
        if name == "diff":
            sub.add_argument(
                "--against", type=int, default=None, help="Version to compare with (default: active)."
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
            # 판정자의 사전·원장 커넥션도 --url 을 따라가야 한 실행이 두 DB 에 걸치지 않는다 (eval 과 같다).
            predictors.set_lexicon_url(args.url)
            polarity = (
                stack.enter_context(registry.open_classifier("polarity", args.impl)) if args.impl else None
            )
            # 규칙이 아닌 구현은 소유 표가 자기 이름을 적어둔 자리에서만 돈다 — 사람이 손으로 치는
            # 명령이라 순서(등록 → 패스)를 아는 곳이 여기밖에 없다 (analysis/polarity/ownership.py).
            if polarity is not None and (blocked := unready(OWNERS, polarity.version, args.scope)):
                print(blocked)
                return 2
            conn = stack.enter_context(_connect(args.url))
        # 아직 아무 단계도 시작하지 못한 거절은 blocked 다 — 실패한 run 과 종료 코드로 갈린다.
        except (ValueError, LookupError, psycopg.Error) as refused:
            print(refused)
            return 2
        outcome = run_stage(conn, args.stage, since=since, scope=args.scope, polarity=polarity)
    print(outcome.note)
    return 0 if outcome.status == "ok" else 1


def _run_retrieval(args: argparse.Namespace) -> int:
    import psycopg

    from analysis.retrieval import corpus, pipeline
    from analysis.retrieval.topics import NoDictionary
    from analysis.retrieval.vectors import StoreMissing

    try:
        since = date.fromisoformat(args.since) if getattr(args, "since", None) else None
        # 인자를 먼저 푼다 -- 인자가 틀렸다고 말하려고 DB 에 붙을 이유가 없고, 연결 뒤에 풀면
        # 옵션 하나가 빠진 하위명령이 연결에 성공한 뒤에야 AttributeError 로 죽는다.
        # embed 에는 --source 가 없다: 하위명령마다 있는 옵션이 다르므로 없으면 기본값을 쓴다.
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
                # 계약 위반은 적재를 막지 않지만 조용히 넘어가서도 안 된다.
                return 0 if not outcome.problems else 1
            if args.action == "eval":
                return _run_retrieval_eval(conn, args, sources, store)
            if args.action == "embed":
                return _run_retrieval_embed(conn, args, store)
            if args.action == "terms":
                return _run_retrieval_terms(conn, args, sources)
            hits = pipeline.search(
                conn, args.query, engine=args.engine, top=args.top, sources=sources, store=store
            )
    # 벡터 파일이 없는 것도, 주제 사전이 아직 안 켜진 것도 실패가 아니라 막힘이다 -- 각각
    # `embed` 와 `cosmai lexicon load/activate` 를 아직 안 돌렸다는 뜻이다.
    except (StoreMissing, NoDictionary) as blocked:
        print(blocked)
        return 2
    if not hits:
        print("결과 없음")
        return 1
    for chunk_id, score, text in hits:
        print(f"{score:8.4f}  {chunk_id}  {text[:120]}")
    return 0


def _run_retrieval_terms(conn: Any, args: argparse.Namespace, sources: tuple[str, ...]) -> int:
    from analysis.retrieval import terms

    scanned = terms.scan(conn, sources=sources)
    print(terms.render(scanned, top=args.top))
    # 문서를 하나도 못 봤으면 표가 아니라 빈 표다 -- `eval` 의 "채점된 질의 0개"와 같은 자리다.
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
        print(f"{args.out} 저장")
    # 질의가 하나도 채점되지 않으면 청크가 비었거나 사전이 안 얹힌 것이다. 조용히 0 을 주지 않는다.
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
    }
    try:
        with conn:
            if args.action == "cards":
                # 카드만 갈래가 다르다 -- 산출이 표가 아니라 stdout 의 마크다운이고 위반 줄이 없다.
                made = cards_collect(conn, args.quarter)
                print(cards_report(made), end="")
                # note 는 stderr 다 -- stdout 이 곧 마크다운 산출물이라, 리다이렉트하면 `.md` 안에
                # `trend cards run=…` 한 줄이 남는다.
                print(made.note, file=sys.stderr)
                for violation in made.violations:
                    print(f"  {violation}", file=sys.stderr)
                # **카드 0건은 1 이 아니다** -- 규칙이 다 돌고 나온 답이다. 1 은 규칙에 걸렸는데 근거
                # 원문이 없어 카드로 서지 못한 셀이 있을 때뿐이다 (#41 이 §민감도 에서 못 박은 자리).
                return 0 if made.status == "ok" else 1
            outcome = acts[args.action](conn)
    # 명부도 스냅샷도 주제 사전도 지표·판정 행도 아직 없는 것은 실패가 아니라 막힘이다 -- 아직 안 세운
    # 것이고, 스냅샷과 사전이 갈린 것(TopicAxisDrift) 역시 사전 판본을 맞추면 같은 명령이 그대로 선다.
    # 코퍼스가 비었는데 지표 행이 남아 있는 것(ShortHistory)도 같은 자리다: 창이 설 분기가 없다.
    # `analysis/judge` 의 SparseGrid·MissingValue 는 여기 없다 -- #41 이 민감도의 반사실 격자에서도
    # 도달 가능한지 확인했고 여전히 아니다(`analysis.trend.rows` 가 주제 × 분기 직사각형을 통째로
    # 내고, 문서가 0인 계열은 행을 만들지 않는다). 그 둘을 잡을 조건은 #40 이 적어 둔 그대로 **다른
    # 생산자가 metrics_topic_quarter 에 쓰는 날**이다.
    except (
        NoPopulation, NoJudgement, NoEvidence, NoBaseline, NoCrosscheck, TopicAxisDrift, NoDictionary,
        ShortHistory,
    ) as blocked:  # fmt: skip
        print(blocked)
        return 2
    print(outcome.note)
    # 답이 표가 아니라 문장인 것은 `sensitivity`·`crosscheck` 둘이다 -- 나머지 셋은 note 와 위반 줄이 전부다.
    # getattr 이 아니라 isinstance 인 것은 타입 체커가 그 갈래를 지게 하려는 것이다.
    if isinstance(outcome, Outcome | CrossOutcome):
        for line in outcome.lines:
            print(line)
    for violation in outcome.violations:
        print(f"  {violation}")
    # 뷰가 무언가 말하면 표는 섰지만 그 표의 뜻이 계약과 다르다 -- 조용히 0 을 주지 않는다.
    return 0 if outcome.status == "ok" else 1


def _run_eval(args: argparse.Namespace) -> int:
    from analysis import predictors, registry
    from analysis.baselines import adoption_misses
    from analysis.evaluate import evaluate, record, render

    # 예측자의 사전 커넥션은 registry 와 무관한 별도 전역(analysis/predictors.py) -- --url 을 안 따라가면
    # eval 한 번이 두 DB(지정한 곳과 운영)에 걸친다. predict 가 열리기 전인 지금 세팅해야 한다.
    predictors.set_lexicon_url(args.url)
    registry.load_implementations()
    try:
        impl = registry.build(args.task, args.impl) if args.impl else registry.get(args.task)
    except LookupError as refused:
        print(refused)
        return 2
    # 기준선 표는 홀드아웃 셋을 먼저 돌려준다 (analysis/baselines.py) — 유료 구현을 --split 없이 부르면
    # 블라인드 홀드아웃이 첫 호출로 나간다. 규율이 아니라 인자로 막는다.
    if args.impl and registry.is_paid(args.task, args.impl) and args.split is None:
        print(f"--impl {args.impl} spends money; pass --split tune or --split holdout")
        return 2
    if impl is None:
        print(
            f"no implementation registered for {args.task!r}; the unit that owns it calls "
            "analysis.registry.register() at import time"
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
    """formats.md 의 사전 CSV → db.lexicon 의 컬럼 순서. kind 열이 있으면 --kind 와 같아야 한다."""
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
        # 알려진 칸 밖의 열은 `extra` 로 간다 -- 룰셋마다 필요한 사실이 다르고(주제 사전의 표기
        # 계열·주제 유형), 그것을 공통 칸에 얹으면 한 컬럼이 룰셋마다 다른 뜻을 갖는다(021).
        # 빈 칸은 값이 아니라 무기입이다: 넣으면 "지정하지 않음"과 "빈 문자열"이 섞인다.
        if "extra" in rows[0]:
            # 남는 열들이 모이는 자리라 그 이름의 열은 자기 자신 안에 들어간다 -- 조용히 버리지 않는다.
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

    from db.lexicon import ASPECT_KIND, activate, active_version, diff, insert_aspects, insert_entities

    with _connect(args.url) as conn, conn.cursor() as cur:
        try:
            if args.action == "load":
                rows = _csv_rows(args.kind, args.csv)
                insert = insert_aspects if args.kind == ASPECT_KIND else insert_entities
                # 새 버전은 꺼진 채로 들어온다 — 교체는 activate 가 한다 (formats.md).
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
            against = args.against if args.against is not None else active_version(cur, args.kind)
            if against is None:
                print(f"{args.kind} has no active version to compare with; pass --against")
                return 2
            d = diff(cur, args.kind, args.version, against)
        # 잘못된 CSV 도 CHECK 위반도 blocked 다 — 트레이스백은 종료 코드 1 이 되어 규약을 깬다.
        except (ValueError, LookupError, psycopg.Error) as refused:
            print(refused)
            return 2
    print(f"{d.kind} v{d.version} vs v{d.against}: +{len(d.added)} -{len(d.removed)} ~{len(d.changed)}")
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
