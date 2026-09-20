"""N10 (#275 review, carried by #280): `flatten` under the role's real `transaction_timeout`.

`tests/collectors/youtube/test_youtube_work_transaction.py` holds `work` against that axis and
nothing held `flatten`: one transaction across `DEFAULT_BATCH_SIZE` = 500 payload reads is the #277
shape `work` was taken out of and `flatten` still has. Every fake in the suite answers instantly, so
"does the pass fit inside the server's own limits" was a question no test asked.

This file asks it twice, and measures rather than argues:

  the shape     the probe `work`'s file uses -- `pg_locks` narrowed to this test's own schema, not a
                bare `pg_stat_activity` filter, which under `-n` answers for another worker's pass --
                says whether a transaction is open while flatten reads a payload off disk.
  the size      a full batch of 500 artifacts under the timeout family the role really carries
                (`transaction_timeout=60s`, `idle_in_transaction_session_timeout=30s`, measured on
                `tubedepth_runtime` for #277). If flatten's one transaction cannot hold a production
                batch, this is what says so -- and the restructure is then its own issue, not a
                change made under this one.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url

from collectors.youtube import cli, flatten
from collectors.youtube.cli import run
from collectors.youtube.payload_store import PayloadStore
from collectors.youtube.storage.tables import artifacts
from tests.collectors.youtube.test_youtube_work_transaction import _open_transactions

pytestmark = pytest.mark.postgres

AT = datetime(2026, 9, 20, 4, tzinfo=UTC)

#: What `db/bootstrap_source.sql` sets on `tubedepth_runtime` and #277 measured in production. Not
#: compressed the way the `work` file's 2s is: the question here is whether a *production-sized*
#: batch fits inside the production number, so the production number is what is set.
TRANSACTION_TIMEOUT = "60s"
IDLE_IN_TRANSACTION_TIMEOUT = "30s"


def _under_the_roles_timeouts(url: str) -> str:
    """The same database and schema with the role's timeout family on the session -- `ALTER ROLE ...
    IN DATABASE` would reach every other test sharing the harness, `options` is the same server
    behaviour scoped to the connection the pass opens."""
    parsed = make_url(url)
    options = str(parsed.query.get("options", ""))
    timeouts = (
        f"-ctransaction_timeout={TRANSACTION_TIMEOUT} "
        f"-cidle_in_transaction_session_timeout={IDLE_IN_TRANSACTION_TIMEOUT}"
    )
    return parsed.update_query_dict({"options": f"{options} {timeouts}".strip()}).render_as_string(
        hide_password=False
    )


def _seed(url: str, payloads: PayloadStore, *, count: int) -> None:
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        rows = []
        for index in range(count):
            stored = payloads.put("video.metadata", {"id": f"v{index}", "title": f"title {index}"})
            rows.append(
                {
                    "identifier": f"art{index:05d}",
                    "kind": "video.metadata",
                    "target": f"v{index}",
                    "fingerprint": f"video.metadata:v{index}",
                    "digest": stored.digest,
                    "byte_count": stored.byte_count,
                    "fetched_at": AT - timedelta(minutes=count - index),
                    "fresh_until": AT,
                    "schema_version": "1",
                }
            )
        conn.execute(sa.insert(artifacts), rows)
    engine.dispose()


class _AsksWhatTheServerSees(PayloadStore):
    """A payload store that asks, at the moment flatten reads an artifact's bytes, whether a
    transaction of this collector's is open on this schema."""

    def __init__(self, root: Path, *, probe_url: str, schema: str) -> None:
        super().__init__(root)
        self._probe_url = probe_url
        self._schema = schema
        self.seen: list[list[str]] = []

    def get(self, kind: str, digest: str) -> Any:
        self.seen.append(_open_transactions(self._probe_url, self._schema))
        return super().get(kind, digest)


def test_flatten_holds_one_transaction_open_across_every_payload_read(
    tubedepth_schema: str, _schema_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The measurement, not a verdict. `work` was taken out of this shape by #277 and `flatten` is
    still in it: the pass's whole batch, every disk read included, lives in one transaction. What
    bounds it is `batch_size`, and this is the test that would notice the day the batch stopped
    being the bound."""
    payloads = _AsksWhatTheServerSees(tmp_path, probe_url=tubedepth_schema, schema=_schema_name)
    _seed(tubedepth_schema, payloads, count=3)
    # `run` builds the store itself from `payload_root`, so the probing one is installed at the seam
    # rather than passed in.
    monkeypatch.setattr(cli, "PayloadStore", lambda root: payloads)

    assert run("flatten", database_url=tubedepth_schema, payload_root=tmp_path, captured_at=AT) == 0

    assert len(payloads.seen) == 3, "one probe per artifact read"
    assert all(states == ["idle in transaction"] for states in payloads.seen), payloads.seen


def test_a_full_batch_fits_inside_the_roles_transaction_timeout(
    tubedepth_schema: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    """A production-sized batch under the production number. A pass that crossed 60s would be
    terminated by the server here exactly as `work` was in production, and since #280 that ends the
    pass at exit 1 with a `failed` run row rather than as a traceback -- so the exit code is what
    says the batch fitted, and the flattened count beside it is what says it did the work."""
    payloads = PayloadStore(tmp_path)
    _seed(tubedepth_schema, payloads, count=flatten.DEFAULT_BATCH_SIZE)

    started = time.monotonic()
    code = run(
        "flatten",
        database_url=_under_the_roles_timeouts(tubedepth_schema),
        payload_root=tmp_path,
        captured_at=AT,
    )
    elapsed = time.monotonic() - started

    assert code == 0, f"a batch of {flatten.DEFAULT_BATCH_SIZE} did not finish inside {TRANSACTION_TIMEOUT}"
    assert f"flattened {flatten.DEFAULT_BATCH_SIZE} artifact(s)" in capsys.readouterr().out
    # The margin, recorded rather than asserted tightly: the bound is 60s and a measurement that
    # needed most of it would mean the batch size, not the machine, is what has to change.
    assert elapsed < 30, f"a full batch took {elapsed:.1f}s of the role's {TRANSACTION_TIMEOUT}"
