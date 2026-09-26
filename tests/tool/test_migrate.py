"""Migration must not publish unverified data or overwrite an existing destination."""

import hashlib
import importlib.util
import io
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location("migrate", Path(__file__).parents[2] / "tool/migrate.py")
assert SPEC is not None and SPEC.loader is not None
migrate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migrate)


@pytest.fixture
def archived(tmp_path):
    def make(name="state/example.txt", content=b"example", wrong_digest=False):
        source = tmp_path / "test.tar.gz"
        manifest = {
            "format": 1,
            "capture": "synthetic",
            "files": {
                name: {
                    "size": len(content),
                    "sha256": "0" * 64 if wrong_digest else hashlib.sha256(content).hexdigest(),
                }
            },
        }
        with tarfile.open(source, "w:gz") as tar:
            for key, value in [(name, content), ("manifest.json", json.dumps(manifest).encode())]:
                entry = tarfile.TarInfo(key)
                entry.size = len(value)
                tar.addfile(entry, io.BytesIO(value))
        return source

    return make


def test_roundtrip_verifies_every_file(archived, tmp_path):
    target = tmp_path / "output"
    target.mkdir()
    archive = archived()
    m, archive_sha = migrate.unpack(archive, target)
    assert (target / "state/example.txt").read_bytes() == b"example"
    assert len(m["files"]) == 1
    assert archive_sha == hashlib.sha256(archive.read_bytes()).hexdigest()


def test_wrong_archive_hash_never_publishes_destination(archived, tmp_path, monkeypatch):
    archive = archived()
    monkeypatch.setattr(migrate, "EXPECTED_ARCHIVE_SHA256", "0" * 64)
    dest = tmp_path / "restored"
    with pytest.raises(ValueError, match="Archive SHA-256"):
        migrate.restore(SimpleNamespace(archive=str(archive), dest=str(dest)))
    assert not dest.exists()
    assert not list(tmp_path.glob(".cosmai-restore-*"))


@pytest.mark.parametrize("name", ["../escaped", "/absolute", "state/../../escaped", "state\\escaped"])
def test_rejects_archive_traversal(archived, tmp_path, name):
    target = tmp_path / "output"
    target.mkdir()
    with pytest.raises(ValueError, match="Unsafe"):
        migrate.unpack(archived(name), target)
    assert not (tmp_path / "escaped").exists()


def test_failed_checksum_never_publishes_destination(archived, tmp_path):
    archive = archived(wrong_digest=True)
    dest = tmp_path / "restored"
    with pytest.raises(ValueError, match="checksum"):
        migrate.restore(SimpleNamespace(archive=str(archive), dest=str(dest)))
    assert not dest.exists()
    assert not list(tmp_path.glob(".cosmai-restore-*"))


def test_restore_refuses_existing_destination(tmp_path):
    with pytest.raises(ValueError, match="must not exist"):
        migrate.restore(SimpleNamespace(dest=str(tmp_path), archive="missing"))


def test_corrupt_gzip_tail_never_publishes_destination(archived, tmp_path):
    archive = archived()
    damaged = bytearray(archive.read_bytes())
    damaged[-1] ^= 1
    archive.write_bytes(damaged)
    dest = tmp_path / "restored"
    with pytest.raises((ValueError, OSError, EOFError, tarfile.TarError)):
        migrate.restore(SimpleNamespace(archive=str(archive), dest=str(dest)))
    assert not dest.exists()


def test_database_restore_refuses_existing_database_before_docker(tmp_path):
    (tmp_path / ".migration").mkdir()
    (tmp_path / ".migration/verified.json").write_text("{}")
    (tmp_path / "database-data").mkdir()
    with pytest.raises(ValueError, match="already exists"):
        migrate.db_restore(SimpleNamespace(dest=str(tmp_path), port=55434, offline=True))


@pytest.mark.postgres
def test_fresh_cluster_restores_foreign_memberships_without_changing_role_options(
    tmp_path, monkeypatch, harness_container
):
    import os
    import subprocess

    suffix = hashlib.sha256(str(tmp_path).encode()).hexdigest()[:10]
    owner, member, grantor = (f"restore_{name}_{suffix}" for name in ("owner", "member", "grantor"))
    (tmp_path / ".migration").mkdir()
    (tmp_path / ".migration/verified.json").write_text("{}")
    (tmp_path / "database").mkdir()
    (tmp_path / "database/app.dump").write_bytes(b"unused")
    (tmp_path / "manifest.json").write_text(json.dumps({"postgres_image": "unused", "table_counts": {}}))
    (tmp_path / "database/roles.sql").write_text(
        f'BEGIN;\nCREATE ROLE "{owner}" NOLOGIN;\n'
        f'CREATE ROLE "{member}" NOLOGIN;\nCREATE ROLE "{grantor}" SUPERUSER NOLOGIN;\n'
        f'GRANT "{owner}" TO "{member}" WITH INHERIT FALSE GRANTED BY "{grantor}";\n'
        "SELECT admin_option, inherit_option, set_option FROM pg_auth_members "
        f"WHERE roleid = '{owner}'::regrole AND member = '{member}'::regrole;\nROLLBACK;\n"
    )
    run = subprocess.run
    real_checked = migrate.checked
    checked_memberships = []

    def check(command, log, stdin=None):
        if command[1] == "run":
            assert (tmp_path / "database-data").stat().st_mode & 0o111 == 0o111
        if "psql" in command:
            assert stdin is not None
            real_checked(
                [
                    "docker",
                    "exec",
                    "-i",
                    harness_container,
                    "psql",
                    "-X",
                    "-U",
                    "fleet",
                    "-d",
                    "fleet",
                    "-At",
                    "-v",
                    "ON_ERROR_STOP=1",
                ],
                log,
                stdin,
            )
            checked_memberships.append("f|f|t" in log.read_text())

    monkeypatch.setattr(migrate, "checked", check)

    def restore_run(command, **kwargs):
        if "pg_isready" in command:
            return SimpleNamespace(returncode=0)
        return run(command, **kwargs)

    monkeypatch.setattr(migrate.subprocess, "run", restore_run)
    previous_umask = os.umask(0o077)
    try:
        migrate.db_restore(SimpleNamespace(dest=str(tmp_path), port=55434, offline=True))
    finally:
        os.umask(previous_umask)
    assert checked_memberships == [True]
