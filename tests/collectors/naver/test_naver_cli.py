"""Exit codes (contracts/entrypoints.md 종료 코드: 0 ok, 1 partial, 2 blocked), the secret-existence
gate (contracts/secrets.md: key names only, exit 2 when missing), and that `cosmai collect naver`
actually reaches this module -- same form as tests/collectors/youtube/test_cli.py (#8)."""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from collectors.naver.cli import FetchSpec, run
from collectors.naver.storage.tables import naver_blog_post, naver_datalab_point, naver_fetch_log, naver_run

pytestmark = pytest.mark.postgres

AT = datetime(2026, 8, 24, 6, 10, tzinfo=UTC)

DATALAB_BODY = {
    "results": [
        {
            "title": "백탁",
            "keywords": ["선크림 백탁", "썬크림 백탁", "선크림 하얗게"],
            "data": [{"period": "2016-01-01", "ratio": 10.4}],
        },
        {"title": "밀림", "keywords": ["선크림 밀림"], "data": [{"period": "2016-01-01", "ratio": 5.1}]},
        {"title": "눈시림", "keywords": ["선크림 눈시림"], "data": [{"period": "2016-01-01", "ratio": 33.7}]},
        {"title": "따가움", "keywords": ["선크림 따가움"], "data": [{"period": "2016-01-01", "ratio": 1.1}]},
        {"title": "끈적임", "keywords": ["선크림 끈적임"], "data": [{"period": "2016-01-01", "ratio": 2.0}]},
    ]
}


def _blog_page(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"items": items, "total": len(items), "display": 100, "start": 1}


class _FakeFetcher:
    """No network: one datalab response, and a single populated page per blog query (empty after)."""

    def __init__(self) -> None:
        self.calls: list[FetchSpec] = []
        self._blog_served: set[str] = set()

    def fetch(self, spec: FetchSpec) -> dict[str, Any]:
        self.calls.append(spec)
        if spec.kind == "datalab":
            return DATALAB_BODY
        if spec.query in self._blog_served:
            return _blog_page([])
        self._blog_served.add(spec.query)
        return _blog_page(
            [
                {
                    "title": f"<b>{spec.query}</b> 후기",
                    "link": f"https://blog.naver.com/x/{spec.query.replace(' ', '_')}",
                    "description": "블로그 본문 발췌",
                    "bloggername": "누군가",
                    "postdate": "20260801",
                }
            ]
        )


class _RaisingFetcher:
    def fetch(self, spec: FetchSpec) -> dict[str, Any]:
        raise RuntimeError("blog search rate limit exceeded (429)")


@pytest.fixture
def secret_file(tmp_path: Path) -> Path:
    path = tmp_path / "env"
    path.write_text(
        "COSMA_SRC_NAVER_BLOG_CLIENT_ID=id\nCOSMA_SRC_NAVER_BLOG_CLIENT_SECRET=secret\n", encoding="utf-8"
    )
    return path


def test_unknown_dataset_is_blocked(needs_runtime_url: str, secret_file: Path):
    assert run("bogus", database_url=needs_runtime_url, secrets_path=secret_file) == 2


def test_missing_secret_key_is_blocked_and_names_the_key(needs_runtime_url: str, tmp_path: Path, capsys):
    empty = tmp_path / "env"
    empty.write_text("", encoding="utf-8")
    code = run("datalab", database_url=needs_runtime_url, secrets_path=empty, fetcher=_FakeFetcher())
    assert code == 2
    out = capsys.readouterr().out
    assert "COSMA_SRC_NAVER_BLOG_CLIENT_ID" in out
    assert "COSMA_SRC_NAVER_BLOG_CLIENT_SECRET" in out


def test_datalab_writes_a_point_per_group_and_month(needs_runtime_url: str, secret_file: Path):
    fetcher = _FakeFetcher()
    code = run(
        "datalab", database_url=needs_runtime_url, secrets_path=secret_file, fetcher=fetcher, captured_at=AT
    )
    assert code == 0

    engine = sa.create_engine(needs_runtime_url)
    with engine.begin() as conn:
        rows = conn.execute(sa.select(naver_datalab_point.c.group_key, naver_datalab_point.c.ratio)).all()
        run_rows = conn.execute(sa.select(naver_run.c.status, naver_run.c.dataset)).all()
        log_rows = conn.execute(sa.select(sa.func.count()).select_from(naver_fetch_log)).scalar_one()
    engine.dispose()

    assert {r.group_key for r in rows} == {"밀림", "눈시림", "백탁", "따가움", "끈적임"}
    assert run_rows == [("ok", "datalab")]
    assert log_rows == 1
    # one request covers keywords.json's one category -- the vendor's own per-request group cap.
    assert len(fetcher.calls) == 1


def test_datalab_shares_one_request_key_within_a_run_and_a_different_one_across_runs(
    needs_runtime_url: str, secret_file: Path
):
    fetcher = _FakeFetcher()
    run("datalab", database_url=needs_runtime_url, secrets_path=secret_file, fetcher=fetcher, captured_at=AT)
    later = datetime(2026, 9, 1, 6, 10, tzinfo=UTC)  # a later run's endDate moves -> a new window
    run(
        "datalab",
        database_url=needs_runtime_url,
        secrets_path=secret_file,
        fetcher=fetcher,
        captured_at=later,
    )

    engine = sa.create_engine(needs_runtime_url)
    with engine.begin() as conn:
        keys = conn.execute(sa.select(naver_datalab_point.c.request_key)).scalars().all()
    engine.dispose()

    # the second run's fresh window rescaled every group's ratio -- all 5 rows now share its key.
    assert len(set(keys)) == 1
    assert keys[0] != ""


def test_datalab_rerun_of_the_same_month_upserts_not_duplicates(needs_runtime_url: str, secret_file: Path):
    fetcher = _FakeFetcher()
    run("datalab", database_url=needs_runtime_url, secrets_path=secret_file, fetcher=fetcher, captured_at=AT)
    run("datalab", database_url=needs_runtime_url, secrets_path=secret_file, fetcher=fetcher, captured_at=AT)

    engine = sa.create_engine(needs_runtime_url)
    with engine.begin() as conn:
        n = conn.execute(sa.select(sa.func.count()).select_from(naver_datalab_point)).scalar_one()
    engine.dispose()
    assert n == 5  # 5 groups x 1 month, not 10


def test_datalab_is_blocked_when_the_fetcher_fails_every_category(needs_runtime_url: str, secret_file: Path):
    code = run(
        "datalab",
        database_url=needs_runtime_url,
        secrets_path=secret_file,
        fetcher=_RaisingFetcher(),
        captured_at=AT,
    )
    assert code == 2


def test_datalab_is_blocked_with_a_message_when_no_fetcher_is_injected(
    needs_runtime_url: str, secret_file: Path, capsys
):
    """Same #95 path as the blog test above, for datalab -- both datasets go through
    `_run_datalab`/`_run_blog`'s own `fetcher.fetch` call, so both need the guard."""
    code = run("datalab", database_url=needs_runtime_url, secrets_path=secret_file, captured_at=AT)
    assert code == 2
    out = capsys.readouterr().out
    assert "no live transport" in out
    assert "#95" in out or "_RaisingFetcher" in out


def test_blog_writes_one_post_per_query_term(needs_runtime_url: str, secret_file: Path):
    fetcher = _FakeFetcher()
    code = run(
        "blog", database_url=needs_runtime_url, secrets_path=secret_file, fetcher=fetcher, captured_at=AT
    )
    assert code == 0

    engine = sa.create_engine(needs_runtime_url)
    with engine.begin() as conn:
        n = conn.execute(sa.select(sa.func.count()).select_from(naver_blog_post)).scalar_one()
        sample = conn.execute(sa.select(naver_blog_post).limit(1)).mappings().one()
    engine.dispose()

    # 5 groups x 3 terms = 15 queries; the fake fetcher serves one distinct post per query term.
    assert n == 15
    assert sample["observed_at_resolution"] == "day"
    assert "<b>" not in sample["title"]


def test_blog_rerun_upserts_on_post_id_not_duplicates(needs_runtime_url: str, secret_file: Path):
    fetcher = _FakeFetcher()
    run("blog", database_url=needs_runtime_url, secrets_path=secret_file, fetcher=fetcher, captured_at=AT)
    fetcher2 = _FakeFetcher()
    run("blog", database_url=needs_runtime_url, secrets_path=secret_file, fetcher=fetcher2, captured_at=AT)

    engine = sa.create_engine(needs_runtime_url)
    with engine.begin() as conn:
        n = conn.execute(sa.select(sa.func.count()).select_from(naver_blog_post)).scalar_one()
    engine.dispose()
    assert n == 15


def test_blog_is_blocked_when_every_query_fails(needs_runtime_url: str, secret_file: Path):
    code = run(
        "blog",
        database_url=needs_runtime_url,
        secrets_path=secret_file,
        fetcher=_RaisingFetcher(),
        captured_at=AT,
    )
    assert code == 2


def test_blog_is_blocked_with_a_message_when_no_fetcher_is_injected(
    needs_runtime_url: str, secret_file: Path, capsys
):
    """#95: the default fetcher (`_RaisingFetcher`, no live transport yet) must end the run exit 2
    with a one-line explanation, not let `NotImplementedError` escape silently or as a traceback.
    Unlike every other test here, this one deliberately does not pass `fetcher=` -- that's the path
    #95 found broken."""
    code = run("blog", database_url=needs_runtime_url, secrets_path=secret_file, captured_at=AT)
    assert code == 2
    out = capsys.readouterr().out
    assert "no live transport" in out
    assert "#95" in out or "_RaisingFetcher" in out


def test_cosmai_collect_naver_reaches_this_module():
    """Not `--help` -- that only proves the parser accepts `naver`. This proves _run_collect's
    dispatch actually imports collectors.naver.cli rather than the "not wired yet" refusal."""
    repo_root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [sys.executable, "-m", "cosmai.cli", "collect", "naver", "--dataset", "bogus"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
    assert "not wired yet" not in result.stdout
    assert "no dataset named" in result.stdout
