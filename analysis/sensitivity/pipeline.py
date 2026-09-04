"""`needs.corpus_*` + 그 run 의 `metrics_topic_quarter` → 민감도·후향 검증 셋 (포크 #41).

**이 파이프라인은 아무것도 쓰지 않는다.** 세 측정이 다루는 것은 반사실 모집단(43채널 전부 · 과거
분기까지만 · 광고 영상을 뺀)이라, 그 행에는 022 의 `panel_role` 어휘에도 `analysis_run` 에도 자리가
없다. 자리를 만드는 것은 추가만의 범위가 아니라 저장된 비율의 뜻을 바꾸는 일이므로, 산출은 표가 아니라
답이다 -- 그리고 읽기 전용이라 운영 DB 에 그대로 돌릴 수 있다.

기저는 다시 센다. 저장된 행을 그대로 쓰지 않는 것은, 세 측정의 차이가 뜻을 가지려면 기저와 변형이
**같은 코드 경로**에서 나와야 하기 때문이다. 대신 다시 센 기저가 저장된 행과 같은지 되묻고, 다르면
그 사실이 먼저 나온다 (`baseline_drift`) -- 그때 이 명령의 모든 차이는 뜻이 없다.

읽기는 문서 단위다(광고 문구·운영자 해시·홍보 링크는 접힌 셈에서 되살릴 수 없다). 그래서
`analysis/trend/pipeline.py` 와 달리 GROUP BY 로 접지 않고, 대신 읽자마자 `conn.commit()` 한다 --
`needs_runtime` 의 `idle_in_transaction_session_timeout` 은 15초다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, LiteralString

import psycopg

from analysis import sensitivity
from analysis.judge.pipeline import METRIC_COLUMNS, SELECT_METRICS
from analysis.sensitivity import (
    AdSensitivity,
    Backtest,
    Frame,
    Population,
    Reaction,
    ShortHistory,
    Video,
)
from analysis.trend.pipeline import (
    CONTENT_TYPE,
    CORPUS_COMMENT,
    CORPUS_LONG,
    COUNTED_FLAGS,
    PANEL_ROLE,
    QUARTER,
    SCOPE,
    TOPIC_FILTER,
    VIDEO,
    NoPopulation,
    note_of,
    topic_axis,
)
from analysis.types import MetricsTopicQuarterRow, PanelSensitivityRow
from db.corpus import active_snapshot
from db.seed import panel as panel_seed

COMMENT_SOURCE = "youtube_comment"
# 패널을 역할로 좁히지 않는다 -- 좁히면 43채널 전부인 반사실이 설 자리가 없다. 역할은 행에 실려 온다.
POPULATION: LiteralString = f"""
WITH panel AS (
  SELECT channel_id, panel_role FROM panel_channel
   WHERE version = %(panel_version)s AND panel_role = ANY(%(roles)s) AND active
), video AS (
  SELECT d.doc_id, d.source_item_id, d.channel_id, p.panel_role, {QUARTER} AS quarter,
         d.text, d.source_metadata ->> 'has_paid_product_placement' AS declared
    FROM corpus_document d
    JOIN panel p ON p.channel_id = d.channel_id
   WHERE d.snapshot_id = %(snapshot)s AND d.source = '{VIDEO}'
     AND d.content_type = '{CORPUS_LONG}'
     AND EXISTS (SELECT 1 FROM corpus_mention m
                  WHERE m.snapshot_id = d.snapshot_id AND m.doc_id = d.doc_id
                    AND m.topic_id = %(topic_filter)s)
)
"""  # noqa: S608

VIDEOS: LiteralString = (
    POPULATION + "SELECT source_item_id, channel_id, panel_role, quarter, declared, text FROM video"
)
VIDEO_TOPICS: LiteralString = (
    POPULATION
    + """
SELECT v.source_item_id, m.topic_id
  FROM video v
  JOIN corpus_mention m ON m.snapshot_id = %(snapshot)s AND m.doc_id = v.doc_id AND m.trend_use
"""
)
# 댓글 술어는 `content_type = 'comment'` 다 -- `source` 하나만 걸면 023 의 부분 인덱스를 못 타고 26만
# 행을 훑는다(#5·#40 실측). 두 술어를 나란히 두는 것은 계약이 그 둘의 동치를 보장하지 않기 때문이다.
COMMENTS: LiteralString = (
    POPULATION
    + f"""
SELECT c.parent_item_id, md5(c.text) AS digest, c.quality_flags = '' AS counted,
       c.source_metadata ->> 'author_channel_hash' AS author, v.channel_id, c.text
  FROM corpus_document c
  JOIN video v ON v.source_item_id = c.parent_item_id
 WHERE c.snapshot_id = %(snapshot)s AND c.content_type = '{CORPUS_COMMENT}'
   AND c.source = '{COMMENT_SOURCE}' AND c.quality_flags = ANY(%(flags)s)
"""
)  # noqa: S608
COMMENT_TOPICS: LiteralString = (
    POPULATION
    + f"""
SELECT c.parent_item_id, md5(c.text) AS digest, m.topic_id
  FROM corpus_document c
  JOIN video v ON v.source_item_id = c.parent_item_id
  JOIN corpus_mention m ON m.snapshot_id = %(snapshot)s AND m.doc_id = c.doc_id AND m.trend_use
 WHERE c.snapshot_id = %(snapshot)s AND c.content_type = '{CORPUS_COMMENT}'
   AND c.source = '{COMMENT_SOURCE}' AND c.quality_flags = ANY(%(flags)s)
"""
)  # noqa: S608
FIND_RUN: LiteralString = "SELECT run_id FROM analysis_run WHERE note = %s ORDER BY run_id LIMIT 1"

DECLARED = "True"  # 코퍼스가 파이썬 bool 을 문자열로 실은 값이다 (source_metadata)


class NoBaseline(LookupError):
    """비교할 기저가 없다. 민감도는 "그 결론이 흔들리는가"라, 결론이 아직 없으면 물을 것이 없다."""


@dataclass(frozen=True)
class Loaded:
    population: Population
    topics: tuple[str, ...]
    frame: Frame
    snapshot_id: int
    stored: tuple[MetricsTopicQuarterRow, ...]


@dataclass(frozen=True)
class Built:
    """세 답 한 벌. 아무것도 쓰지 않으므로 이것이 산출의 전부다."""

    run_id: int
    snapshot_id: int
    panel_version: int
    panel: tuple[PanelSensitivityRow, ...]
    back: Backtest
    ad: AdSensitivity
    violations: tuple[str, ...] = ()

    @property
    def flipped(self) -> list[PanelSensitivityRow]:
        return sensitivity.flipped(self.panel)

    @property
    def flipped_cells(self) -> int:
        return sum(row.flipped_cells for row in self.ad.rows)

    @property
    def status(self) -> str:
        """`ok` = 답이 계산됐다. **흔들림은 여기 실리지 않는다.**

        흔들린다는 것은 이 명령이 답하려고 존재하는 발견이지 실행의 실패가 아니고, 그 신호는 `note` 의
        `panel_flips=`·`ad_flips=` 와 표가 이미 싣는다. 종료 코드에 얹으면 두 가지가 깨진다 -- 전량에서
        1 이 평상 상태라 `set -e` 셸 한 줄이 정상 실행을 실패로 읽고, "발견했다"와 "믿지 마라"가 같은
        수가 된다. `partial` 은 뒤의 하나만 뜻한다 (계약 §종료 코드, ydc 도 같은 자리다).
        """
        return "ok" if not self.violations else "partial"

    @property
    def note(self) -> str:
        tail = f" partial:{len(self.violations)} violations" if self.violations else ""
        return (
            f"trend sensitivity run={self.run_id} snapshot={self.snapshot_id} "
            f"panel=v{self.panel_version} panel_flips={len(self.flipped)} "
            f"backtest={len(self.back.rows)} hits={sum(1 for r in self.back.rows if r.hit)}/"
            f"{sum(1 for r in self.back.rows if r.hit_level)} base={self.back.base_rate:.0f}%/"
            f"{self.back.base_level_rate:.0f}% ad_flips={self.flipped_cells}{tail}"
        )


@dataclass(frozen=True)
class Outcome:
    """`trend quarter`·`trend judge` 의 결과와 같은 세 칸(note·status·violations)을 낸다 -- CLI 가 세
    명령을 한 자리에서 다루려면 답의 모양이 같아야 한다. `lines` 만 이 명령의 것이다."""

    built: Built
    lines: tuple[str, ...] = ()

    @property
    def note(self) -> str:
        return self.built.note

    @property
    def status(self) -> str:
        return self.built.status

    @property
    def violations(self) -> tuple[str, ...]:
        return self.built.violations


def _params(snapshot: int, version: int) -> dict[str, Any]:
    return {
        "snapshot": snapshot,
        "panel_version": version,
        "roles": list(sensitivity.ALL_ROLES),
        "topic_filter": TOPIC_FILTER,
        "flags": list(COUNTED_FLAGS),
    }


def _videos(cur: psycopg.Cursor[Any], params: dict[str, Any]) -> list[Video]:
    cur.execute(VIDEO_TOPICS, dict(params))
    topics: dict[str, list[str]] = {}
    for item_id, topic in cur.fetchall():
        topics.setdefault(item_id, []).append(topic)
    cur.execute(VIDEOS, dict(params))
    made: list[Video] = []
    for item_id, channel_id, role, quarter, declared, text in cur.fetchall():
        made.append(
            Video(
                item_id=item_id,
                channel_id=channel_id,
                panel_role=role,
                quarter=quarter,
                topics=tuple(topics.get(item_id, ())),
                declared=declared == DECLARED,
                matched=bool(sensitivity.AD_RE.search(text or "")),
            )
        )
    return made


def _reactions(cur: psycopg.Cursor[Any], params: dict[str, Any]) -> list[Reaction]:
    """(부모 영상, 텍스트) 묶음으로 접는다. 접는 자리가 제외의 단위이자 ydc 의 `(video_id, text)` 키다."""
    cur.execute(COMMENT_TOPICS, dict(params))
    topics: dict[tuple[str, str], set[str]] = {}
    for parent, digest, topic in cur.fetchall():
        topics.setdefault((parent, digest), set()).add(topic)
    cur.execute(COMMENTS, dict(params))
    counted: dict[tuple[str, str], int] = {}
    documents: dict[tuple[str, str], int] = {}
    creator: dict[tuple[str, str], bool] = {}
    promo: dict[tuple[str, str], bool] = {}
    for parent, digest, is_counted, author, channel_id, text in cur.fetchall():
        key = (parent, digest)
        documents[key] = documents.get(key, 0) + 1
        counted[key] = counted.get(key, 0) + int(bool(is_counted))
        # 운영자 댓글이 먼저다 -- 판매 링크를 단 운영자 고정 댓글이 두 계열에 겹쳐 들면 제외 집합의
        # 합이 각 집합의 합과 달라진다 (ydc 의 elif 와 같은 자리).
        owner = author == sensitivity.creator_hash(channel_id)
        creator[key] = creator.get(key, False) or owner
        promo[key] = promo.get(key, False) or (not owner and bool(sensitivity.PROMO_RE.search(text or "")))
    return [
        Reaction(
            parent_item_id=parent,
            digest=digest,
            counted=counted[(parent, digest)],
            documents=documents[(parent, digest)],
            topics=tuple(sorted(topics.get((parent, digest), ()))),
            creator=creator[(parent, digest)],
            promo=promo[(parent, digest)],
        )
        for parent, digest in documents
    ]


def _stored(cur: psycopg.Cursor[Any], run_id: int, version: int) -> list[MetricsTopicQuarterRow]:
    cur.execute(SELECT_METRICS, (run_id, SCOPE, version, PANEL_ROLE))
    made: list[MetricsTopicQuarterRow] = []
    for row in cur.fetchall():
        fields = dict(zip(METRIC_COLUMNS, row, strict=True))
        for name in ("composition", "velocity_yoy", "persistence", "unique_ratio", "channel_diffusion"):
            fields[name] = None if fields[name] is None else float(fields[name])
        made.append(MetricsTopicQuarterRow(**fields))
    return made


def load(
    conn: psycopg.Connection[Any],
    *,
    scope: str = SCOPE,
    snapshot_id: int | None = None,
    panel_version: int | None = None,
) -> Loaded:
    """읽고, 트랜잭션을 닫는다. run 을 찾는 길은 `trend quarter`·`trend judge` 와 같은 하나다."""
    with conn.cursor() as cur:
        version = panel_version if panel_version is not None else panel_seed.active_version(cur)
        snapshot = snapshot_id if snapshot_id is not None else active_snapshot(cur)
        if version is None:
            raise NoPopulation("no active panel roster; run `python -m db.seed --only panel` first")
        if snapshot is None:
            raise NoPopulation("no active corpus snapshot; run `python -m db.corpus load <dir>` first")
        cur.execute(FIND_RUN, (note_of(scope, snapshot, version),))
        found = cur.fetchone()
        if found is None:
            raise NoBaseline(
                f"no quarter run for {scope!r} on snapshot {snapshot}; run `cosmai trend quarter`"
            )
        run_id = int(found[0])
        topics = topic_axis(conn, cur, snapshot)
        params = _params(snapshot, version)
        videos = _videos(cur, params)
        reactions = _reactions(cur, params)
        stored = _stored(cur, run_id, version)
    conn.commit()
    if not stored:
        raise NoBaseline(f"run {run_id} has no metrics_topic_quarter row to be sensitive about")
    return Loaded(
        population=Population(tuple(videos), tuple(reactions)),
        topics=tuple(topics),
        frame=Frame(run_id=run_id, scope=scope, content_type=CONTENT_TYPE, panel_version=version),
        snapshot_id=snapshot,
        stored=tuple(stored),
    )


# 후향 검증이라 부르려면 사례가 둘은 있어야 한다 (기획안 08.25 "후향 검증 사례 2건 이상"). ydc
# `backtest.py` 도 이 하나에만 종료 코드 1 을 쓴다.
MIN_CASES = 2


def _drift(base: list[MetricsTopicQuarterRow], stored: tuple[MetricsTopicQuarterRow, ...]) -> list[str]:
    """다시 센 기저가 저장된 행과 같은가. 다르면 세 측정의 차이는 전부 뜻을 잃는다."""
    key = lambda row: (row.source, row.topic_key, row.quarter)  # noqa: E731
    mine = {key(row): row for row in base}
    theirs = {key(row): row for row in stored}
    if set(mine) != set(theirs):
        missing = sorted(set(mine) ^ set(theirs))[:5]
        return [f"baseline_drift - the recount and metrics_topic_quarter disagree on cells {missing}"]
    return [
        f"baseline_drift {cell[2]} {cell[0]}/{cell[1]} recount != stored row"
        for cell, row in mine.items()
        if row != theirs[cell]
    ][:5]


def build(
    conn: psycopg.Connection[Any],
    *,
    scope: str = SCOPE,
    snapshot_id: int | None = None,
    panel_version: int | None = None,
) -> Built:
    read = load(conn, scope=scope, snapshot_id=snapshot_id, panel_version=panel_version)
    base = sensitivity.metrics(read.population, read.topics, read.frame)
    back = sensitivity.backtest(read.population, read.topics, read.frame, base)
    # 위반 줄은 전부 같은 말을 한다: **이 산출을 믿지 마라.** 흔들림은 여기 들지 않는다.
    violations = _drift(base, read.stored)
    if len(back.rows) < MIN_CASES:
        violations.append(
            f"thin_backtest - {len(back.rows)} directional cell(s) over {len(back.cutoffs)} cutoff(s); "
            f"fewer than {MIN_CASES} is not a backtest"
        )
    return Built(
        run_id=read.frame.run_id,
        snapshot_id=read.snapshot_id,
        panel_version=read.frame.panel_version,
        panel=tuple(sensitivity.panel_sensitivity(read.population, read.topics)),
        back=back,
        ad=sensitivity.ad_sensitivity(read.population, read.topics, read.frame, base),
        violations=tuple(violations),
    )


def render(built: Built) -> list[str]:
    """사람이 읽는 답. ydc 세 스크립트의 요약과 같은 문장을 낸다."""
    ad = built.ad
    lines = [
        f"패널  {len(built.panel)}셀(주제 × 소스) 중 판정 대상 "
        f"{sum(1 for r in built.panel if r.sample_ok)}셀 · 방향이 뒤집힌 것 {len(built.flipped)}셀",
    ]
    lines += [
        f"  뒤집힘 {row.source} / {row.topic_key} : {row.delta_product_pp:+.2f} -> {row.delta_all_pp:+.2f}"
        for row in built.flipped
    ]
    hits = sum(1 for row in built.back.rows if row.hit)
    level = sum(1 for row in built.back.rows if row.hit_level)
    total = len(built.back.rows)
    lines.append(
        f"후향  검증 시점 {len(built.back.cutoffs)}개 · 방향성 판정 {total}건 · "
        f"기준 A {hits}건 vs 기저율 {built.back.base_rate:.0f}% · "
        f"기준 B {level}건 vs 기저율 {built.back.base_level_rate:.0f}%"
    )
    lines += [
        f"  {row.cutoff} {row.source} / {row.topic_key} {row.trend_type} : "
        f"{row.before_pp:.2f} -> {row.after_pp:.2f} "
        f"{'적중' if row.hit else '실패'}/{'적중' if row.hit_level else '실패'} ({row.expected})"
        for row in built.back.rows
    ]
    lines.append(
        f"표시  장문 {ad.videos:,}편 중 광고·협찬 {ad.ad_videos:,}편"
        f"(신고 {ad.declared:,} · 문구 {ad.matched:,}) · 댓글 {ad.comments:,}건 중 "
        f"운영자 {ad.creator_comments:,} · 홍보 {ad.promo_comments:,}"
    )
    for variant in sensitivity.VARIANTS:
        mine = [row for row in ad.rows if row.variant == variant]
        moved = [row for row in mine if abs(row.diff_pp) >= sensitivity.MATERIAL_PP]
        worst = max(mine, key=lambda row: abs(row.diff_pp))
        lines.append(
            f"  [{variant}] 유형이 뒤집힌 셀 {sum(row.flipped_cells for row in mine)} · "
            f"표본 미달로 사라진 셀 {ad.lost_cells[variant]} · "
            f"{sensitivity.MATERIAL_PP}%p 이상 움직인 주제 {len(moved)} · "
            f"최대 {worst.diff_pp:+.2f}%p ({worst.topic_key})"
        )
    return lines


def run(
    conn: psycopg.Connection[Any],
    *,
    scope: str = SCOPE,
    snapshot_id: int | None = None,
    panel_version: int | None = None,
) -> Outcome:
    built = build(conn, scope=scope, snapshot_id=snapshot_id, panel_version=panel_version)
    return Outcome(built=built, lines=tuple(render(built)))


__all__ = [
    "MIN_CASES",
    "Built",
    "Loaded",
    "NoBaseline",
    "NoPopulation",
    "Outcome",
    "ShortHistory",
    "build",
    "load",
    "render",
    "run",
]
