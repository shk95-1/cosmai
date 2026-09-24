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
