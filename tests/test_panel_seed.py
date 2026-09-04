"""패널 명부 43채널이 needs 로 들어가고, 재실행이 아무것도 바꾸지 않으며, 활성 판본은 하나다 (포크 #31).

`active` 는 행 단위라 두 판본이 동시에 켜질 수 있고, 그러면 `panel_channel (version, panel_role)
WHERE active` 부분 인덱스를 타는 분모가 이중으로 세어진다. 부분 유니크 인덱스로는 "활성 행 중
distinct version 이 하나"를 적을 수 없으므로(#3 리뷰 L6 이 이 불변식을 적재기에 지웠다) 여기서
적재기가 그것을 지는지 묻는다.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest

from db import seed
from db.seed import panel
from db.seed._common import EVAL_DIR, REPO_ROOT, connect, read_csv

SEED_CSV = EVAL_DIR / "panel" / "channels_v1.csv"
SLICE_CSV = REPO_ROOT / "analysis" / "slices" / "ydc" / "seeds" / "channels_v1.csv"
EXPECTED = {"panel_roster": 1, "panel_channel": 43}
ROLES = {"product": 34, "expert": 9}  # contracts/formats.md §패널 명부 CSV 의 v1 패널 열
CSV_COLUMNS = 11  # 파일은 11열, 표로 가는 것은 6열 (같은 계약)

SNAPSHOT = (
    "SELECT version, note, seeded_at FROM panel_roster ORDER BY version",
    "SELECT version, channel_id, panel_role, handle, channel_title, role_basis, source_list,"
    " active, seeded_at FROM panel_channel ORDER BY version, channel_id",
)


def _snapshot(cur: psycopg.Cursor[Any]) -> list[list[tuple[Any, ...]]]:
    """행이 다시 쓰였는지 세는 자리 -- 개수만 보면 UPDATE 가 값을 바꾼 재실행이 초록으로 지나간다."""
    out = []
    for query in SNAPSHOT:
        cur.execute(query)  # type: ignore[arg-type]
        out.append(cur.fetchall())
    return out


def _roles(cur: psycopg.Cursor[Any]) -> dict[str, int]:
    cur.execute("SELECT panel_role, count(*) FROM panel_channel WHERE active GROUP BY panel_role")
    return {role: int(n) for role, n in cur.fetchall()}


# ---------- 원본이 슬라이스 밖에 있다 ----------
def test_the_panel_seed_csv_sits_outside_the_slice_that_9_deletes():
    """#9 가 `analysis/slices/ydc/` 를 지웠다 -- 그 안에 원본을 남겼다면 같이 사라졌을 자리다."""
    assert SEED_CSV.is_file()
    assert not SLICE_CSV.exists()


def test_the_seed_csv_reads_without_a_bom_special_case():
    """`db/seed/_common.read_csv` 는 utf-8 로 연다 -- BOM 이 남으면 첫 열 이름이 `﻿channel_id` 가
    되고, 적재기는 KeyError 하나로 죽는다."""
    assert not SEED_CSV.read_bytes().startswith(b"\xef\xbb\xbf")
    csv_rows = read_csv(SEED_CSV)
    assert len(csv_rows) == 43
    assert len(csv_rows[0]) == CSV_COLUMNS
    assert csv_rows[0]["channel_id"].startswith("UC")


# ---------- 값이 들어간다 · 재실행이 아무것도 바꾸지 않는다 ----------
@pytest.mark.postgres
def test_the_seed_loads_43_panel_channels_and_a_rerun_changes_nothing(needs_runtime_url: str):
    assert seed.run_all(needs_runtime_url, only=("panel",)) == EXPECTED
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        before = _snapshot(cur)
        assert _roles(cur) == ROLES
    assert seed.run_all(needs_runtime_url, only=("panel",)) == EXPECTED
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        # seeded_at 까지 같아야 한다: 값이 같은 UPDATE 도 행을 다시 쓰고, 그것은 변경 0 이 아니다.
        assert _snapshot(cur) == before
        assert _roles(cur) == ROLES


@pytest.mark.postgres
def test_the_seed_keeps_the_six_columns_the_contract_maps_and_drops_the_other_five(
    needs_runtime_url: str,
):
    """계약이 버린다고 적은 다섯 열(team_rank·team_role·channel_published_at·video_count_at_seed·
    subscriber_count_at_seed)은 표에 자리가 없다 -- 여섯 열이 실제로 채워졌는지만 여기서 센다."""
    seed.run_all(needs_runtime_url, only=("panel",))
    source = {r["channel_id"]: r for r in read_csv(SEED_CSV)}
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT channel_id, panel_role, handle, channel_title, role_basis, source_list"
            " FROM panel_channel WHERE version = %s",
            (panel.PANEL_VERSION,),
        )
        loaded = {row[0]: row for row in cur.fetchall()}
        cur.execute("SELECT version, note FROM panel_roster")
        assert cur.fetchall() == [(panel.PANEL_VERSION, panel.PANEL_NOTE)]
    assert set(loaded) == set(source)
    assert all(
        loaded[cid]
        == (
            cid,
            row["panel_role"],
            row["handle"] or None,
            row["channel_title"],
            row["role_basis"],
            row["source_list"],
        )
        for cid, row in source.items()
    )


# ---------- 활성 판본은 언제나 하나 ----------
@pytest.mark.postgres
def test_a_second_panel_version_turns_the_first_one_off_in_one_statement(needs_runtime_url: str):
    seed.run_all(needs_runtime_url, only=("panel",))
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        assert panel.active_version(cur) == panel.PANEL_VERSION
        panel.insert(cur, panel.rows(EVAL_DIR)[:5], version=2, note="seed:test-v2")
        # Mid-transaction, both versions are active -- 027's constraint trigger is deferred to
        # commit, so this in-flight state is legal and the swap below is what makes it so.
        cur.execute("SELECT count(DISTINCT version) FROM panel_channel WHERE active")
        assert cur.fetchone() == (2,)
        panel.activate(cur, 2)
        conn.commit()
        assert panel.active_version(cur) == 2
        cur.execute("SELECT count(*) FROM panel_channel WHERE active")
        assert cur.fetchone() == (5,)
        # 두 번째 activate 는 아무 행도 다시 쓰지 않는다.
        assert panel.activate(cur, 2) == 0


@pytest.mark.postgres
def test_activating_a_version_that_has_no_rows_is_refused(needs_runtime_url: str):
    """빈 판본을 켜면 `SET active = (version = n)` 이 명부를 통째로 끄고 분모를 0 으로 만든다."""
    seed.run_all(needs_runtime_url, only=("panel",))
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        with pytest.raises(LookupError):
            panel.activate(cur, 9)
        conn.rollback()
        assert panel.active_version(cur) == panel.PANEL_VERSION


@pytest.mark.postgres
def test_two_active_versions_are_refused_rather_than_counted_twice(needs_runtime_url: str):
    """부분 인덱스는 이 상태를 막지 못한다. 분모를 묻는 자리가 조용히 86 을 세는 대신 멈춘다."""
    seed.run_all(needs_runtime_url, only=("panel",))
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        panel.insert(cur, panel.rows(EVAL_DIR), version=2, note="seed:test-v2")
        cur.execute("UPDATE panel_channel SET active = true")
        with pytest.raises(ValueError):
            panel.active_version(cur)
        # This transaction never commits, so 027's deferred constraint trigger never fires here --
        # the Python guard above is what catches the double-active state in this test.
        conn.rollback()


@pytest.mark.postgres
def test_a_hand_update_leaving_two_versions_active_is_rejected_at_commit(needs_runtime_url: str):
    """027: a hand `UPDATE` that bypasses the loader is refused when the transaction commits, not
    only by the Python guard -- the DB itself now enforces this (#34's Facts)."""
    seed.run_all(needs_runtime_url, only=("panel",))
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        panel.insert(cur, panel.rows(EVAL_DIR)[:5], version=2, note="seed:test-v2")
        cur.execute("UPDATE panel_channel SET active = true WHERE version = %s", (2,))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.commit()
        conn.rollback()
        assert panel.active_version(cur) == panel.PANEL_VERSION
