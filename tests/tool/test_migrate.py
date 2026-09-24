"""Migration must not publish unauthenticated data or overwrite an existing destination."""

import hashlib
import importlib.util
import io
import json
import shutil
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location("migrate", Path(__file__).parents[2] / "tool/migrate.py")
assert SPEC is not None and SPEC.loader is not None
migrate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migrate)


@pytest.fixture
def encrypted(tmp_path):
    if not shutil.which("gpg"):
        pytest.skip("GnuPG is a migration-host prerequisite")

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
        archive = tmp_path / "test.gpg"
        home = tmp_path / "gnupg"
        home.mkdir(mode=0o700, exist_ok=True)
        subprocess.run(
            [
                "gpg",
                "--homedir",
                str(home),
                "--batch",
                "--yes",
                "--pinentry-mode",
                "loopback",
                "--passphrase-fd",
                "0",
                "--compress-algo",
                "none",
                "--symmetric",
                "--output",
                str(archive),
                str(source),
            ],
            input=b"synthetic-test-password\n",
            check=True,
            capture_output=True,
        )
        return archive

    return make


def test_roundtrip_verifies_every_file(encrypted, tmp_path):
    target = tmp_path / "output"
    target.mkdir()
    m = migrate.unpack(encrypted(), target, b"synthetic-test-password")
    assert (target / "state/example.txt").read_bytes() == b"example"
    assert len(m["files"]) == 1


@pytest.mark.parametrize("name", ["../escaped", "/absolute", "state/../../escaped", "state\\escaped"])
def test_rejects_archive_traversal(encrypted, tmp_path, name):
    target = tmp_path / "output"
    target.mkdir()
    with pytest.raises(ValueError, match="Unsafe"):
        migrate.unpack(encrypted(name), target, b"synthetic-test-password")
    assert not (tmp_path / "escaped").exists()


def test_failed_checksum_never_publishes_destination(encrypted, tmp_path):
    archive = encrypted(wrong_digest=True)
    passfile = tmp_path / "password"
    passfile.write_bytes(b"synthetic-test-password")
    passfile.chmod(0o600)
    dest = tmp_path / "restored"
    with pytest.raises(ValueError, match="checksum"):
        migrate.restore(SimpleNamespace(archive=str(archive), dest=str(dest), passphrase_file=str(passfile)))
    assert not dest.exists()
    assert not list(tmp_path.glob(".cosmai-restore-*"))


def test_restore_refuses_existing_destination_before_reading_password(tmp_path):
    with pytest.raises(ValueError, match="must not exist"):
        migrate.restore(SimpleNamespace(dest=str(tmp_path), archive="missing", passphrase_file=None))


def test_corrupt_encrypted_tail_never_publishes_destination(encrypted, tmp_path):
    archive = encrypted()
    damaged = bytearray(archive.read_bytes())
    damaged[-1] ^= 1
    archive.write_bytes(damaged)
    passfile = tmp_path / "password"
    passfile.write_bytes(b"synthetic-test-password")
    passfile.chmod(0o600)
    dest = tmp_path / "restored"
    with pytest.raises((ValueError, tarfile.TarError)):
        migrate.restore(SimpleNamespace(archive=str(archive), dest=str(dest), passphrase_file=str(passfile)))
    assert not dest.exists()


def test_database_restore_refuses_existing_database_before_docker(tmp_path):
    (tmp_path / ".migration").mkdir()
    (tmp_path / ".migration/verified.json").write_text("{}")
    (tmp_path / "database-data").mkdir()
    with pytest.raises(ValueError, match="already exists"):
        migrate.db_restore(SimpleNamespace(dest=str(tmp_path), port=55434, offline=True))
