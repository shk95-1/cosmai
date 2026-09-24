#!/usr/bin/env python3
"""Verify and restore a password-encrypted Cosmai migration bundle (#330).

Requires Python 3.11+, GnuPG, Git, and (for db-restore) Docker with PostgreSQL 18.
No collector, paid API, old service, or production migration is started.
"""

import argparse
import getpass
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath

EXPECTED_ARCHIVE_SHA256 = ""


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def safe_name(name):
    parts = PurePosixPath(name)
    if not name or parts.is_absolute() or ".." in parts.parts or "\\" in name:
        raise ValueError("Unsafe archive path")
    return parts.as_posix()


def password(args):
    if args.passphrase_file:
        p = Path(args.passphrase_file)
        if p.is_symlink() or p.stat().st_mode & 0o077:
            raise ValueError("Passphrase file must be private and not a symlink")
        value = p.read_bytes()
    else:
        value = getpass.getpass("Migration password: ").encode()
    if not value or b"\n" in value or b"\r" in value:
        raise ValueError("Empty or multiline password")
    return value


def unpack(archive, target, secret):
    """Stream authenticated decryption; publish nothing until GPG and hashes pass."""
    if EXPECTED_ARCHIVE_SHA256 and digest(archive) != EXPECTED_ARCHIVE_SHA256:
        raise ValueError("Archive SHA-256 differs from this delivery script")
    readfd, writefd = os.pipe()
    os.write(writefd, secret + b"\n")
    os.close(writefd)
    seen = {}
    manifest = None
    with tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(
            [
                "gpg",
                "--batch",
                "--pinentry-mode",
                "loopback",
                "--no-symkey-cache",
                "--passphrase-fd",
                str(readfd),
                "--decrypt",
                str(archive),
            ],
            pass_fds=(readfd,),
            stdout=subprocess.PIPE,
            stderr=errors,
        )
        os.close(readfd)
        try:
            with tarfile.open(fileobj=proc.stdout, mode="r|gz") as tar:
                for entry in tar:
                    name = safe_name(entry.name)
                    if not entry.isfile() or name in seen:
                        raise ValueError("Only unique regular files are allowed in this bundle")
                    source = tar.extractfile(entry)
                    h = hashlib.sha256()
                    size = 0
                    output = None
                    data = bytearray() if name == "manifest.json" else None
                    if target is not None:
                        path = target / name
                        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                        output = path.open("xb")
                    try:
                        while block := source.read(1024 * 1024):
                            h.update(block)
                            size += len(block)
                            if output:
                                output.write(block)
                            if data is not None:
                                if size > 32 * 1024 * 1024:
                                    raise ValueError("Manifest too large")
                                data.extend(block)
                    finally:
                        if output:
                            output.close()
                            path.chmod(0o700 if entry.mode & 0o111 else 0o600)
                    seen[name] = {"sha256": h.hexdigest(), "size": size}
                    if data is not None:
                        manifest = json.loads(data)
            # Drain to authenticated EOF even if tar stopped at its end marker.
            while proc.stdout.read(1024 * 1024):
                pass
            if proc.wait() != 0:
                raise ValueError("Decryption/authentication failed")
        finally:
            proc.stdout.close()
            if proc.poll() is None:
                proc.kill()
                proc.wait()
    if not manifest or manifest.get("format") != 1:
        raise ValueError("Unsupported or missing manifest")
    seen.pop("manifest.json", None)
    if seen != manifest["files"]:
        raise ValueError("Bundle file inventory or checksum mismatch")
    return manifest


def private_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value, indent=2) + "\n")
    path.chmod(0o600)


def checked(command, log, stdin=None):
    with log.open("ab") as output:
        result = subprocess.run(command, stdin=stdin, stdout=output, stderr=output)
    if result.returncode:
        raise RuntimeError("Command failed; inspect the private operation log in the destination")


def restore(args):
    dest = Path(args.dest).expanduser().absolute()
    if dest.exists() or dest.is_symlink():
        raise ValueError("Restore destination must not exist (never overwrite a checkout or database)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".cosmai-restore-", dir=dest.parent))
    try:
        manifest = unpack(Path(args.archive), temporary, password(args))
        # The target only becomes visible after full authentication and checksum verification.
        if dest.exists() or dest.is_symlink():
            raise ValueError("Destination appeared while restoring")
        temporary.rename(dest)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    private_json(
        dest / ".migration" / "verified.json",
        {
            "archive_sha256": digest(args.archive),
            "files": len(manifest["files"]),
            "capture": manifest["capture"],
        },
    )
    print("Data verified and extracted. Next: checkout, then db-restore using this destination.")


def checkout(args):
    dest = Path(args.dest).expanduser().absolute()
    manifest = json.loads((dest / "manifest.json").read_text())
    if not (dest / ".migration" / "verified.json").exists():
        raise ValueError("Restore and verify the archive first")
    log = dest / ".migration" / "checkout.log"
    for repo in manifest["repositories"]:
        path = dest / "workspace" / safe_name(repo["path"])
        if path.exists():
            raise ValueError("Checkout path already exists; inspect partial operation before retrying")
        path.parent.mkdir(parents=True, exist_ok=True)
        checked(["git", "clone", "--no-checkout", repo["url"], str(path)], log)
        checked(["git", "-C", str(path), "checkout", "--detach", repo["sha"]], log)
        if repo["path"] == "cosmai":
            checked(["git", "-C", str(path), "checkout", "-B", "main", repo["sha"]], log)
            bundle = dest / "code" / "unpublished.bundle"
            checked(
                [
                    "git",
                    "-C",
                    str(path),
                    "fetch",
                    str(bundle),
                    "refs/heads/collectors/youtube/289-clean-comment-backfill:refs/heads/collectors/youtube/289-clean-comment-backfill",
                ],
                log,
            )
            checked(["git", "-C", str(path), "config", "core.hooksPath", ".githooks"], log)
    # Archived local-only files overlay exact pinned checkouts. Never use old absolute host paths.
    shutil.copytree(
        dest / "state" / "workspace", dest / "workspace", dirs_exist_ok=True, copy_function=os.link
    )
    main = dest / "workspace" / "cosmai"
    env = main / "stack" / ".env"
    env.parent.mkdir(parents=True, exist_ok=True)
    env.write_text(
        "COSMAI_SECRET_FILE_HOST=" + str(dest / "state" / "secrets" / "cosmai.env") + "\n"
        "COSMAI_PG_DATA_DIR=" + str(dest / "database-data") + "\n"
        "COMMERCE_BROWSER_PROFILE_DIR=../var/browser-profiles\n"
        "NEEDS_PORTAL_PORT=3003\nCOSMAI_LLM_BUDGET_USD=0\nCOSMAI_LLM_CHAIN=\n"
    )
    env.chmod(0o600)
    print("Pinned code and unpublished branch restored. Dependencies are rebuilt from repository lockfiles.")
    print("Collectors remain off. Old runtime configurations are historical files, not startup instructions.")


def db_restore(args):
    dest = Path(args.dest).expanduser().absolute()
    if not args.offline and not 1024 <= args.port <= 65535:
        raise ValueError("Choose an unprivileged port between 1024 and 65535")
    if not (dest / ".migration" / "verified.json").exists():
        raise ValueError("Restore and verify the archive first")
    database = dest / "database-data"
    if database.exists() or database.is_symlink():
        raise ValueError("Database destination already exists; refusing to overwrite")
    manifest = json.loads((dest / "manifest.json").read_text())
    suffix = hashlib.sha256(str(dest).encode()).hexdigest()[:10]
    name = "cosmai-migration-" + suffix
    log = dest / ".migration" / "database.log"
    bootstrap = dest / ".migration" / "bootstrap.env"
    bootstrap.write_text(
        "POSTGRES_USER=migration_bootstrap\nPOSTGRES_DB=postgres\nPOSTGRES_PASSWORD="
        + secrets.token_hex(32)
        + "\n"
    )
    bootstrap.chmod(0o600)
    database.mkdir(mode=0o700)
    command = [
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "--restart=no",
        "--env-file",
        str(bootstrap),
        "-v",
        str(database) + ":/var/lib/postgresql",
    ]
    if args.offline:
        command.extend(["--network", "none"])
    else:
        command.extend(["-p", "127.0.0.1:" + str(args.port) + ":5432"])
    command.append(manifest["postgres_image"])
    checked(command, log)
    bootstrap.unlink()
    private_json(
        dest / ".migration" / "database.json",
        {"container": name, "port": None if args.offline else args.port, "state": "restoring"},
    )
    try:
        for _ in range(120):
            ready = subprocess.run(
                ["docker", "exec", name, "pg_isready", "-U", "migration_bootstrap", "-d", "postgres"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Fresh database did not become ready")
        with (dest / "database" / "roles.sql").open("rb") as source:
            checked(
                [
                    "docker",
                    "exec",
                    "-i",
                    name,
                    "psql",
                    "-X",
                    "-U",
                    "migration_bootstrap",
                    "-d",
                    "postgres",
                    "-v",
                    "ON_ERROR_STOP=1",
                ],
                log,
                source,
            )
        with (dest / "database" / "app.dump").open("rb") as source:
            checked(
                [
                    "docker",
                    "exec",
                    "-i",
                    name,
                    "pg_restore",
                    "-U",
                    "migration_bootstrap",
                    "-d",
                    "postgres",
                    "--exit-on-error",
                    "--create",
                    "--no-tablespaces",
                ],
                log,
                source,
            )
        for table, expected in manifest["table_counts"].items():
            schema, relation = table.split(".", 1)
            sql = (
                'SELECT count(*) FROM "'
                + schema.replace('"', '""')
                + '"."'
                + relation.replace('"', '""')
                + '";'
            )
            got = subprocess.check_output(
                [
                    "docker",
                    "exec",
                    name,
                    "psql",
                    "-X",
                    "-U",
                    "migration_bootstrap",
                    "-d",
                    "app",
                    "-Atqc",
                    sql,
                ],
                text=True,
            ).strip()
            if int(got) != expected:
                raise ValueError("Restored table count differs from the captured snapshot: " + table)
        private_json(
            dest / ".migration" / "database.json",
            {
                "container": name,
                "port": None if args.offline else args.port,
                "state": "verified",
                "tables": len(manifest["table_counts"]),
            },
        )
        print("Fresh PostgreSQL restored and all captured table counts verified.")
        print("Database container: " + name + "; collectors remain off.")
    except BaseException:
        # This name belongs exclusively to this newly created destination. Keep data/logs for diagnosis.
        subprocess.run(["docker", "stop", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        raise


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("verify", "restore"):
        p = sub.add_parser(name)
        p.add_argument("archive")
        p.add_argument(
            "--passphrase-file", help="Private temporary file; omit to enter password interactively"
        )
        if name == "restore":
            p.add_argument("--dest", required=True)
    p = sub.add_parser("checkout")
    p.add_argument("--dest", required=True)
    p = sub.add_parser("db-restore")
    p.add_argument("--dest", required=True)
    p.add_argument("--port", type=int, default=55434)
    p.add_argument("--offline", action="store_true", help="No network/host port; for isolated rehearsal")
    args = parser.parse_args()
    if args.command == "verify":
        m = unpack(Path(args.archive), None, password(args))
        print("Verified encrypted archive and " + str(len(m["files"])) + " file hashes.")
        print("Snapshot: " + m["capture"])
    else:
        {"restore": restore, "checkout": checkout, "db-restore": db_restore}[args.command](args)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError, tarfile.TarError) as error:
        print("Migration stopped: " + str(error), file=sys.stderr)
        sys.exit(1)
