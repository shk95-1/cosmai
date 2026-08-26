"""ydc 가 넘긴 유튜브 코퍼스 스냅샷을 `needs.corpus_*` 로 반입한다 (포크 #4).

원본은 `~/github_prj/Main/archive/yt-handoff/` 의 CSV 세 장(261,317 · 105,358 · 43행)이다.
`archive/` 는 수정 금지(STATE.md §3)이고 174M 를 레포에 복사하지도 않으므로, 이 적재기는 **경로를
인자로 받는다** -- `db/seed` 처럼 레포 안의 고정 자리를 읽지 않는 유일한 이유다.

세 가지가 이 파일의 모양을 정했다.

1. **덮이지 않는다.** 재수집(#38)은 같은 유일키(`source + source_item_id`)로 같은 영상을 다시
   가져오지만 2026-08-19 의 조회수·좋아요·댓글은 재현되지 않는다. 그래서 관측 판본이 키의 맨 앞에
   있고(`corpus_document` PK), 재수집분은 다른 `snapshot_id` 로 들어와 옛 행 옆에 선다.
2. **배치·페이징.** `needs_runtime` 은 statement_timeout 30s · transaction_timeout 60s 아래에 있다
   (`db/bootstrap.sql`). 26만 행을 한 트랜잭션에 넣으면 그 벽에 부딪히므로 페이지마다 커밋한다 --
   `analysis/retrieval/corpus.py` 가 읽기에서 같은 이유로 같은 모양을 쓴다. 그래서 이 `load()` 는
   `db/seed/*` 와 달리 커서가 아니라 **연결**을 받는다: 커밋하는 쪽이 트랜잭션을 소유해야 한다.
3. **재실행 멱등.** 모든 INSERT 가 `ON CONFLICT DO NOTHING` 이라 두 번 돌려도 값이 다시 쓰이지
   않는다(`imported_at` 도 첫 적재 값으로 남는다).
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any, LiteralString

import psycopg

from db.corpus import contract
from db.seed import panel
from db.seed._common import as_timestamp, counts, opt

TABLES = ("corpus_snapshot", "corpus_document", "corpus_mention")

SNAPSHOT_ID = 1
SNAPSHOT_LABEL = "yt-handoff-20260819"
SNAPSHOT_NOTE = "ydc 인계 코퍼스. 원본 archive/yt-handoff/ (읽기 전용)"

# 한 페이지의 행 수. 30초 안에 끝나는 executemany 를 목표로 잡은 값이고, 크게 잡으면
# statement_timeout 에, 작게 잡으면 왕복 횟수에 진다.
BATCH = 1000

# 174M CSV 한 줄에 영상 설명 전체가 들어온다 -- csv 기본 상한(128KiB)으로는 _csv.Error 로 죽는다.
csv.field_size_limit(10**7)

Progress = Callable[[str, int], None]

SNAPSHOT_SQL: LiteralString = """
INSERT INTO corpus_snapshot (snapshot_id, label, produced_by, source_runs, collected_at, note)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (snapshot_id) DO NOTHING
"""
# 재적재가 값이 같은 행을 다시 쓰지 않는다 -- "변경 0" 이 rowcount 로 읽힌다.
ACTIVATE_SQL: LiteralString = """
UPDATE corpus_snapshot SET active = (snapshot_id = %s)
WHERE active IS DISTINCT FROM (snapshot_id = %s)
"""
SNAPSHOT_COUNT_SQL: LiteralString = "SELECT count(*) FROM corpus_document WHERE snapshot_id = %s"
ACTIVE_SQL: LiteralString = "SELECT snapshot_id FROM corpus_snapshot WHERE active"

# doc_id 는 생성 열이라 여기 없다 (023).
DOCUMENT_SQL: LiteralString = """
INSERT INTO corpus_document
  (snapshot_id, source, source_item_id, content_type, parent_item_id, channel_id,
   published_at, url, text, quality_flags, source_metadata, collected_at, source_run)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
ON CONFLICT (snapshot_id, source, source_item_id) DO NOTHING
"""
MENTION_SQL: LiteralString = """
INSERT INTO corpus_mention
  (snapshot_id, doc_id, topic_id, topic_type, trend_use, matched_term, span_start)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (snapshot_id, doc_id, topic_id) DO NOTHING
"""
PANEL_SQL: LiteralString = "SELECT channel_id, panel_role FROM panel_channel WHERE version = %s AND active"


class CorpusMismatch(ValueError):
    """반입할 행이 이미 계약에 선 사실과 어긋난다. 숫자를 맞추려 비트는 대신 멈춘다."""


def read_manifest(source_dir: Path) -> dict[str, Any]:
    """매니페스트를 읽고 그 규칙이 계약이 진 문장과 같은지 되묻는다 (`db/corpus/contract.py`)."""
    manifest: dict[str, Any] = json.loads((source_dir / "manifest.json").read_text(encoding="utf-8"))
    contract.check(manifest)
    return manifest


def read_csv(path: Path) -> Iterator[dict[str, str]]:
    """스트리밍. 26만 행을 통째로 dict 리스트로 올리면 원문(174M)이 두 벌 메모리에 선다.
    `utf-8-sig`: 이 CSV 들은 BOM 을 달고 있어서 utf-8 로 열면 첫 열 이름이 `\\ufeffdoc_id` 가 된다."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            yield {k: (v or "") for k, v in row.items()}


def runs_by_collected_at(manifest: dict[str, Any]) -> dict[str, str]:
    """수집 시각 -> run_id. 문서 행에는 런 id 가 없고 `source_metadata.collected_at` 만 있는데,
    두 런의 시각이 다르므로 이 표 하나로 행마다 어느 런에서 왔는지가 복원된다."""
    return {run["collected_at"]: run["run_id"] for run in manifest["source_run_manifests"]}


def document_row(row: dict[str, str], snapshot_id: int, runs: dict[str, str]) -> tuple[Any, ...]:
    raw = row["source_metadata"] or "{}"
    metadata: dict[str, Any] = json.loads(raw)
    collected_at = metadata.get("collected_at")
    if collected_at not in runs:
        raise CorpusMismatch(
            f"{row['doc_id']}: collected_at {collected_at!r} belongs to no run in the manifest"
        )
    # 규칙 1 의 뒷문장을 여기서도 되묻는다: CSV 의 doc_id 와 생성 열이 갈리면 mention 조인이 조용히 빈다.
    expected = f"{row['source']}:{row['source_item_id']}"
    if row["doc_id"] != expected:
        raise CorpusMismatch(f"doc_id {row['doc_id']!r} is not {expected!r} (manifest rule 1)")
    return (
        snapshot_id,
        row["source"],
        row["source_item_id"],
        row["content_type"],
        opt(row["parent_item_id"]),
        row["channel_id"],
        as_timestamp(row["published_at"]),
        opt(row["url"]),
        row["text"],
        row["quality_flags"],
        raw,
        as_timestamp(collected_at),
        runs[collected_at],
    )


def mention_row(row: dict[str, str], snapshot_id: int) -> tuple[Any, ...]:
    return (
        snapshot_id,
        row["doc_id"],
        row["topic_id"],
        row["topic_type"],
        row["trend_use"].lower() == "true",
        opt(row["matched_term"]),
        int(row["span_start"]) if row["span_start"] else None,
    )


def check_channels(cur: psycopg.Cursor[Any], source_dir: Path, panel_version: int) -> int:
    """코퍼스가 언급하는 채널이 전부 활성 명부에 같은 역할로 있는가.

    `channel.csv` 를 표로 만들지 않는 이유가 이 함수다. 채널의 역할은 분모를 정하는 값이고
    (`contracts/formats.md` §패널 명부 CSV), 그것이 두 표에 살면 두 분모가 생겨 나중 것이 앞선 것과
    조용히 갈린다. 그래서 명부는 `panel_channel` 하나로 두고, 반입은 어긋남을 **거절**한다.
    """
    cur.execute(PANEL_SQL, (panel_version,))
    roster = {channel_id: role for channel_id, role in cur.fetchall()}
    rows = list(read_csv(source_dir / "channel.csv"))
    problems = [
        f"{row['channel_id']}: corpus says {row['panel_role']}, roster says {roster.get(row['channel_id'])}"
        for row in rows
        if roster.get(row["channel_id"]) != row["panel_role"]
    ]
    if problems:
        raise CorpusMismatch(
            f"channel.csv disagrees with the active panel roster (version {panel_version}): "
            + "; ".join(problems)
        )
    # 명부 크기가 아니라 이 파일의 행수를 돌려준다 -- 매니페스트의 table_counts 가 세는 것이 그쪽이다.
    return len(rows)


def _tally(
    rows: Iterator[dict[str, str]],
    counts: dict[str, int],
    name: str,
    by_type: Counter[str] | None = None,
) -> Iterator[dict[str, str]]:
    """흐르는 CSV 를 세면서 넘긴다 -- 174M 를 세자고 한 번 더 읽으면 반입이 두 배로 든다."""
    for row in rows:
        counts[name] = counts.get(name, 0) + 1
        if by_type is not None:
            by_type[row["content_type"]] += 1
        yield row


def _pages(rows: Iterator[tuple[Any, ...]], batch: int) -> Iterator[list[tuple[Any, ...]]]:
    page: list[tuple[Any, ...]] = []
    for row in rows:
        page.append(row)
        if len(page) >= batch:
            yield page
            page = []
    if page:
        yield page


def copy_pages(
    conn: psycopg.Connection[Any],
    statement: LiteralString,
    rows: Iterator[tuple[Any, ...]],
    *,
    batch: int,
    label: str,
    progress: Progress | None,
) -> int:
    """페이지마다 한 트랜잭션. 반환값은 실제로 들어간 행 수라, 재실행이면 0 이 나온다."""
    inserted = 0
    seen = 0
    for page in _pages(rows, batch):
        with conn.cursor() as cur:
            cur.executemany(statement, page)
            inserted += max(cur.rowcount, 0)
        conn.commit()
        seen += len(page)
        if progress:
            progress(label, seen)
    return inserted


def insert_snapshot(
    cur: psycopg.Cursor[Any], manifest: dict[str, Any], snapshot_id: int, label: str, note: str
) -> None:
    runs = runs_by_collected_at(manifest)
    cur.execute(
        SNAPSHOT_SQL,
        (
            snapshot_id,
            label,
            manifest.get("produced_by"),
            sorted(runs.values()),
            as_timestamp(min(runs)),
            note,
        ),
    )


def activate(cur: psycopg.Cursor[Any], snapshot_id: int) -> int:
    """이 판본만 켠다. 문서가 없는 판본을 켜면 분석이 빈 코퍼스를 오류 없이 읽으므로 거절한다
    (`db/seed/panel.activate` 와 같은 자리)."""
    cur.execute(SNAPSHOT_COUNT_SQL, (snapshot_id,))
    row = cur.fetchone()
    if not (row and row[0]):
        raise LookupError(f"corpus_document has no rows at snapshot {snapshot_id}; nothing to activate")
    cur.execute(ACTIVATE_SQL, (snapshot_id, snapshot_id))
    return max(cur.rowcount, 0)


def active_snapshot(cur: psycopg.Cursor[Any]) -> int | None:
    """활성 스냅샷. 둘일 수는 없다 -- 023 의 부분 유니크 인덱스가 그것을 DB 에서 막는다."""
    cur.execute(ACTIVE_SQL)
    rows: Sequence[tuple[Any, ...]] = cur.fetchall()
    return int(rows[0][0]) if rows else None


def load(
    conn: psycopg.Connection[Any],
    source_dir: Path,
    *,
    snapshot_id: int = SNAPSHOT_ID,
    label: str = SNAPSHOT_LABEL,
    note: str = SNAPSHOT_NOTE,
    panel_version: int | None = None,
    activate_snapshot: bool = True,
    batch: int = BATCH,
    progress: Progress | None = None,
) -> dict[str, int]:
    """세 CSV 를 한 스냅샷으로 반입하고 표마다 `count(*)` 를 돌려준다."""
    manifest = read_manifest(source_dir)
    runs = runs_by_collected_at(manifest)
    with conn.cursor() as cur:
        version = panel_version if panel_version is not None else panel.active_version(cur)
        if version is None:
            raise CorpusMismatch("no active panel roster; load db/seed --only panel first (fork #31)")
        table_counts = {"channel.csv": check_channels(cur, source_dir, version)}
        insert_snapshot(cur, manifest, snapshot_id, label, note)
    conn.commit()

    by_type: Counter[str] = Counter()
    copy_pages(
        conn,
        DOCUMENT_SQL,
        (
            document_row(row, snapshot_id, runs)
            for row in _tally(read_csv(source_dir / "document.csv"), table_counts, "document.csv", by_type)
        ),
        batch=batch,
        label="corpus_document",
        progress=progress,
    )
    copy_pages(
        conn,
        MENTION_SQL,
        (
            mention_row(row, snapshot_id)
            for row in _tally(read_csv(source_dir / "mention.csv"), table_counts, "mention.csv")
        ),
        batch=batch,
        label="corpus_mention",
        progress=progress,
    )
    # 켜기 **전에** 대조한다: 뒤라면 분석은 이미 그 판본을 읽고 있다. 행은 남지만 스냅샷마다 다른
    # 키를 쓰므로 (023) 옆에 설 뿐 아무것도 덮지 않는다.
    contract.check_counts(manifest, {"table_counts": table_counts, "documents_by_content_type": by_type})
    with conn.cursor() as cur:
        # 판본을 켜는 것은 "분석이 이제 이것을 읽는다"는 뜻이라, 옛 스냅샷 옆에 한 벌 더 쌓기만 하는
        # 반입(#38)은 끄고 부를 수 있어야 한다. 행은 어느 쪽이든 덮이지 않는다.
        if activate_snapshot:
            activate(cur, snapshot_id)
        result = counts(cur, TABLES)
    conn.commit()
    return result
