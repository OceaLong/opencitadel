#!/usr/bin/env python3
"""Offline-capable manifest verification and isolated Compose recovery drills.

Only Python's standard library and the Docker CLI are required. Configuration
is read into memory; credentials are never serialized into the manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
POSTGRES = "opencitadel-postgres"
PAYLOADS = ("postgres.dump", "roles.sql", "table-counts.tsv", "minio-data.tar.gz")
COUNTS_SQL = r"""SELECT format('SELECT %L || chr(9) || count(*)::text FROM %I.%I;',
 schemaname || '.' || tablename, schemaname, tablename)
 FROM pg_tables WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
 ORDER BY schemaname, tablename;
\gexec
"""


class BackupError(Exception):
    pass


def docker(*args, data=None, output=None, env=None):
    result = subprocess.run(
        ["docker", *args],
        input=data,
        stdout=output or subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL if data is None else None,
        env=env,
        check=False,
    )
    if result.returncode:
        # Docker/config errors can contain credentials. Keep command stderr private.
        raise BackupError("Docker operation failed: " + args[0])
    return (result.stdout or b"").decode()


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def digest(path):
    with path.open("rb") as source:
        result = hashlib.sha256()
        for block in iter(lambda: source.read(1024 * 1024), b""):
            result.update(block)
        return result.hexdigest()


def object_index(path):
    result = {}
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts or not (member.isfile() or member.isdir()):
                raise BackupError("Unsafe object archive member")
            if member.isdir():
                continue
            normalized = str(name)
            if normalized in result:
                raise BackupError("Duplicate object archive member")
            contents = archive.extractfile(member)
            checksum = hashlib.sha256()
            for block in iter(lambda contents=contents: contents.read(1024 * 1024), b""):
                checksum.update(block)
            result[normalized] = {"size": member.size, "sha256": checksum.hexdigest()}
    return result


def verify(directory):
    directory = Path(directory).resolve()
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("version") != 1 or manifest.get("status") != "complete":
        raise BackupError("Backup is partial or has an unsupported manifest version")
    if set(manifest.get("files", {})) != set(PAYLOADS):
        raise BackupError("Required backup payload is missing")
    for name in PAYLOADS:
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise BackupError("Missing or linked payload: " + name)
        expected = manifest["files"][name]
        if path.stat().st_size != expected["size"] or digest(path) != expected["sha256"]:
            raise BackupError("Payload checksum mismatch: " + name)
    with (directory / "postgres.dump").open("rb") as dump:
        signature = dump.read(5)
    if signature != b"PGDMP":
        raise BackupError("Invalid PostgreSQL custom dump")
    if object_index(directory / "minio-data.tar.gz") != manifest["objects"]:
        raise BackupError("Object content index mismatch")
    return manifest


def backup(directory):
    directory = Path(directory).resolve()
    project = os.environ.get("COMPOSE_PROJECT_NAME", "opencitadel")
    compose = ["compose", "--project-name", project, "--profile", "local"]
    config = json.loads(docker(*compose, "config", "--format", "json"))
    services = config["services"]
    database = services[POSTGRES]
    database_env = database["environment"]
    username, dbname = database_env["POSTGRES_USER"], database_env["POSTGRES_DB"]
    api_env = services["opencitadel-api"].get("environment", {})
    if (
        api_env.get("STORAGE_PROVIDER", "cos") != "minio"
        or api_env.get("MINIO_ENDPOINT") != "opencitadel-minio:9000"
    ):
        raise BackupError(
            "Only the local Compose MinIO backend is supported; external storage needs a provider snapshot workflow"
        )
    mounts = [v for v in services["opencitadel-minio"]["volumes"] if v["target"] == "/data"]
    if len(mounts) != 1 or mounts[0]["type"] != "volume":
        raise BackupError("MinIO must use a single named data volume")
    volume = config["volumes"][mounts[0]["source"]]["name"]
    docker("volume", "inspect", volume)
    running = docker(*compose, "ps", "--status", "running", "--services").splitlines()
    if POSTGRES not in running:
        raise BackupError("PostgreSQL must be running")
    # Stop every application-side service, including overrides/one-off workers.
    stopped = [service for service in running if service not in {POSTGRES, "opencitadel-redis"}]
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    manifest = {
        "version": 1,
        "status": "partial",
        "created_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017 -- host Python 3.9
        "source": {
            "project": config.get("name", project),
            "database": dbname,
            "database_user": username,
            "postgres_image": database["image"],
            "object_volume": volume,
            "minio_image": services["opencitadel-minio"]["image"],
        },
        "consistency": "application services and MinIO stopped; PostgreSQL logical snapshot",
        "quiesced_services": stopped,
        "files": {},
        "objects": {},
    }
    write_json(directory / "manifest.json", manifest)
    successful = False
    try:
        if stopped:
            docker(*compose, "stop", "--timeout", "60", *stopped)
        with (directory / "postgres.dump").open("wb") as output:
            docker(
                *compose,
                "exec",
                "-T",
                POSTGRES,
                "pg_dump",
                "-U",
                username,
                "-d",
                dbname,
                "-Fc",
                output=output,
            )
        with (directory / "roles.sql").open("wb") as output:
            docker(
                *compose,
                "exec",
                "-T",
                POSTGRES,
                "pg_dumpall",
                "-U",
                username,
                "--roles-only",
                "--no-role-passwords",
                output=output,
            )
        counts = docker(
            *compose,
            "exec",
            "-T",
            POSTGRES,
            "psql",
            "-X",
            "-qAt",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            username,
            "-d",
            dbname,
            data=COUNTS_SQL.encode(),
        )
        (directory / "table-counts.tsv").write_text(counts)
        docker(
            "run",
            "--rm",
            "--network",
            "none",
            "--mount",
            f"type=volume,source={volume},target=/data,readonly",
            "--mount",
            f"type=bind,source={directory},target=/backup",
            os.environ.get("OPENCITADEL_BACKUP_ARCHIVE_IMAGE", "alpine:3.20"),
            "tar",
            "-czf",
            "/backup/minio-data.tar.gz",
            "-C",
            "/data",
            ".",
        )
        manifest["objects"] = object_index(directory / "minio-data.tar.gz")
        manifest["files"] = {
            name: {"size": (directory / name).stat().st_size, "sha256": digest(directory / name)}
            for name in PAYLOADS
        }
        # Verify structure before advertising this as a completed backup.
        with (directory / "postgres.dump").open("rb") as dump:
            signature = dump.read(5)
        if signature != b"PGDMP":
            raise BackupError("PostgreSQL produced an invalid dump")
        successful = True
    finally:
        # This also runs after partial stop, dump, archive, or signal failure.
        # If restart fails, status remains partial and the caller receives error.
        if stopped:
            docker(*compose, "start", *stopped)
        if successful:
            manifest["status"] = "complete"
        write_json(directory / "manifest.json", manifest)
    print("Backup complete: " + str(directory))


def restore(directory, target):
    directory = Path(directory).resolve()
    manifest = verify(directory)  # MUST precede all Docker mutations.
    if not re.fullmatch(r"restore_[a-z0-9][a-z0-9_-]{0,40}", target):
        raise BackupError("Target must be a new restore_<name> identifier")
    if target == manifest["source"]["project"]:
        raise BackupError("Cannot restore into the source project")
    names = docker("volume", "ls", "--format", "{{.Name}}").splitlines()
    if any(name == target or name.startswith(target + "_") for name in names):
        raise BackupError("Destination volumes already exist; use a new target")
    if docker("ps", "-aq", "--filter", f"name=^/{target}_").strip():
        raise BackupError("Destination containers already exist; use a new target")
    invocation = secrets.token_hex(8)
    pgvolume, objects, container = (
        target + "_" + invocation + "_postgres",
        target + "_" + invocation + "_objects",
        target + "_postgres",
    )
    restore_user = "restore_" + secrets.token_hex(12)
    dbname = manifest["source"]["database"]
    pgimage = manifest["source"]["postgres_image"]
    for volume in (pgvolume, objects):
        docker("volume", "create", "--label", f"opencitadel.restore={target}", volume)
    # New standalone container has no network and no published ports. Random
    # recovery superuser is distinct from all normal source roles.
    environment = dict(os.environ, POSTGRES_PASSWORD=secrets.token_urlsafe(32))
    report = {
        "target": target,
        "status": "partial",
        "application_smoke_verified": False,
        "database_volume": pgvolume,
        "object_volume": objects,
        "container": container,
        "source_manifest_sha256": digest(directory / "manifest.json"),
        "recovery_superuser": restore_user,
    }
    report_path = directory / f"restore-{target}.json"
    write_json(report_path, report)
    started = False
    try:
        # Even an unexpectedly reused volume must be empty before PostgreSQL writes.
        docker(
            "run",
            "--rm",
            "--network",
            "none",
            "--mount",
            f"type=volume,source={pgvolume},target=/data",
            os.environ.get("OPENCITADEL_BACKUP_ARCHIVE_IMAGE", "alpine:3.20"),
            "sh",
            "-ec",
            'test -z "$(ls -A /data)"',
        )
        docker(
            "run",
            "-d",
            "--name",
            container,
            "--network",
            "none",
            "--label",
            f"opencitadel.restore={target}",
            "-e",
            "POSTGRES_PASSWORD",
            "-e",
            f"POSTGRES_USER={restore_user}",
            "-e",
            f"POSTGRES_DB={dbname}",
            "--mount",
            f"type=volume,source={pgvolume},target=/var/lib/postgresql/data",
            "--mount",
            f"type=bind,source={directory},target=/backup,readonly",
            pgimage,
            env=environment,
        )
        started = True
        for attempt in range(60):
            try:
                docker(
                    "exec",
                    container,
                    "pg_isready",
                    "-h",
                    "127.0.0.1",
                    "-U",
                    restore_user,
                    "-d",
                    dbname,
                )
                break
            except BackupError:
                if attempt == 59:
                    raise BackupError("Recovery PostgreSQL did not become ready") from None
                time.sleep(1)
        docker(
            "exec",
            "-i",
            container,
            "psql",
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            restore_user,
            "-d",
            dbname,
            "-f",
            "/backup/roles.sql",
        )
        docker(
            "exec",
            container,
            "pg_restore",
            "--exit-on-error",
            "-U",
            restore_user,
            "-d",
            dbname,
            "/backup/postgres.dump",
        )
        counts = docker(
            "exec",
            "-i",
            container,
            "psql",
            "-X",
            "-qAt",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            restore_user,
            "-d",
            dbname,
            data=COUNTS_SQL.encode(),
        )
        if counts != (directory / "table-counts.tsv").read_text():
            raise BackupError("Restored table row counts differ from backup")
        docker(
            "run",
            "--rm",
            "--network",
            "none",
            "--mount",
            f"type=volume,source={objects},target=/data",
            "--mount",
            f"type=bind,source={directory},target=/backup,readonly",
            os.environ.get("OPENCITADEL_BACKUP_ARCHIVE_IMAGE", "alpine:3.20"),
            "sh",
            "-ec",
            'test -z "$(ls -A /data)"; tar -xzf /backup/minio-data.tar.gz -C /data',
        )
        with tempfile.TemporaryDirectory(prefix="opencitadel-restore-") as temporary:
            docker(
                "run",
                "--rm",
                "--network",
                "none",
                "--mount",
                f"type=volume,source={objects},target=/data,readonly",
                "--mount",
                f"type=bind,source={temporary},target=/backup",
                os.environ.get("OPENCITADEL_BACKUP_ARCHIVE_IMAGE", "alpine:3.20"),
                "tar",
                "-czf",
                "/backup/restored.tar.gz",
                "-C",
                "/data",
                ".",
            )
            if object_index(Path(temporary) / "restored.tar.gz") != manifest["objects"]:
                raise BackupError("Restored object contents differ from backup")
        report["status"] = "payload_verified"
    finally:
        # Preserve volumes for investigation and application acceptance. No
        # cleanup command ever targets the original project or data volumes.
        if started:
            docker("stop", container)
        write_json(report_path, report)
    print("Recovery payload verified; application smoke tests remain: " + str(report_path))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("backup", "verify", "restore"):
        child = sub.add_parser(command)
        child.add_argument("directory")
        if command == "restore":
            child.add_argument("target")
    arguments = parser.parse_args()

    def interrupted(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        if arguments.command == "backup":
            backup(arguments.directory)
        elif arguments.command == "restore":
            restore(arguments.directory, arguments.target)
        else:
            verify(arguments.directory)
            print("Backup manifest and payload hashes verified")
    except (
        BackupError,
        OSError,
        ValueError,
        KeyError,
        tarfile.TarError,
        KeyboardInterrupt,
    ) as error:
        print("Backup/recovery failed: " + (str(error) or "interrupted"), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
