"""Host-only separate historical DB fixture; no shared database downgrade."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import tarfile
import time
from pathlib import Path

from scripts.acceptance.strict_bridge import BridgeError, atomic_json, digest, read_json

LEGACY_REVISION = "97ae6574c80ba6f7fd0d98778223c3fb3610ce07"


def archive_legacy(root, evidence):
    destination = Path(evidence) / "strict-legacy-source.tar"
    with destination.open("xb") as output:
        result = subprocess.run(
            ["git", "archive", "--format=tar", LEGACY_REVISION, "api"],
            cwd=root,
            stdout=output,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=30,
        )
    if result.returncode:
        raise BridgeError("historical source archive unavailable")
    directory = Path(evidence) / "strict-legacy-source"
    directory.mkdir()
    with tarfile.open(destination) as archive:
        for member in archive.getmembers():
            path = Path(member.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or not path.parts
                or path.parts[0] != "api"
                or member.issym()
                or member.islnk()
            ):
                raise BridgeError("unsafe historical source archive")
        archive.extractall(directory, filter="data")
    return directory / "api", digest(destination)


def assert_fixture_container(document, *, binding, image, name):
    labels = document.get("Config", {}).get("Labels", {})
    expected = {
        "com.docker.compose.project": binding["project"],
        "com.opencitadel.acceptance.project": binding["project"],
        "com.opencitadel.acceptance.run": binding["run_id"],
        "com.opencitadel.acceptance.invocation": binding["invocation_id"],
        "com.opencitadel.acceptance.fixture": "strict-legacy",
    }
    if (
        document.get("Image") != image
        or document.get("Name", "").lstrip("/") != name
        or any(labels.get(k) != v for k, v in expected.items())
    ):
        raise BridgeError("legacy fixture ownership changed")
    if not document.get("Id") or document.get("HostConfig", {}).get("PortBindings"):
        raise BridgeError("legacy fixture identity/port isolation invalid")


def write_private_env(path, values):
    if any("\n" in value or "\r" in value for value in values.values()):
        raise BridgeError("invalid private fixture environment")
    with Path(path).open("x", encoding="utf8") as stream:
        os.chmod(path, 0o600)
        stream.write("".join(key + "=" + value + "\n" for key, value in values.items()))
        stream.flush()
        os.fsync(stream.fileno())


def run_legacy_fixture(*, root, evidence, binding, runner, run):
    """Invoked only by the authorized future full-stack gate, never collection."""
    root, evidence = Path(root), Path(evidence)
    archived, archive_digest = archive_legacy(root, evidence)
    suffix = binding["invocation_id"].replace("-", "")
    name = "strict-legacy-" + suffix
    database = "strict_legacy_" + suffix
    main_ids = run((*runner._compose, "ps", "-q", "opencitadel-postgres")).stdout.split()
    if len(main_ids) != 1:
        raise BridgeError("owned PostgreSQL image origin is ambiguous")
    main = json.loads(run(("docker", "inspect", "--format", "{{json .}}", main_ids[0])).stdout)
    labels = main["Config"]["Labels"]
    if (
        labels.get("com.docker.compose.project") != binding["project"]
        or labels.get("com.opencitadel.acceptance.run") != binding["run_id"]
        or labels.get("com.docker.compose.service") != "opencitadel-postgres"
    ):
        raise BridgeError("PostgreSQL source image is not acceptance owned")
    image = main["Image"]
    network_name = binding["project"] + "_opencitadel-network"
    network = json.loads(
        run(("docker", "network", "inspect", "--format", "{{json .}}", network_name)).stdout
    )
    if (
        network["Labels"].get("com.docker.compose.project") != binding["project"]
        or network["Labels"].get("com.opencitadel.acceptance.run") != binding["run_id"]
    ):
        raise BridgeError("legacy fixture network is foreign")
    network_id = network["Id"]
    label_args = []
    for key, value in {
        "com.docker.compose.project": binding["project"],
        "com.opencitadel.acceptance.project": binding["project"],
        "com.opencitadel.acceptance.run": binding["run_id"],
        "com.opencitadel.acceptance.invocation": binding["invocation_id"],
        "com.opencitadel.acceptance.fixture": "strict-legacy",
    }.items():
        label_args.extend(("--label", f"{key}={value}"))
    admin, migration, api, kernel = (secrets.token_hex(32) for _ in range(4))
    pg_env, phase_env = evidence / "strict-legacy-pg.env", evidence / "strict-legacy-phase.env"
    signing = secrets.token_hex(32)
    obligations = []
    legacy = {
        "host": name,
        "database": database,
        "postgres_image": image,
        "network_id": network_id,
        "historical_revision": LEGACY_REVISION,
        "historical_archive_sha256": archive_digest,
        "role_init_sha256": digest(
            root / "deploy/helm/opencitadel/files/postgres/init-app-role.sh"
        ),
        "main_database_distinct": True,
    }
    input_document = {"binding": binding, "legacy": legacy}
    atomic_json(evidence / "strict-legacy-input.json", input_document)

    def dispose(container_name, expected_image):
        ids = run(
            ("docker", "ps", "-aq", "--no-trunc", "--filter", "name=^/" + container_name + "$")
        ).stdout.split()
        if not ids:
            return
        if len(ids) != 1:
            raise BridgeError("ambiguous owned legacy container")
        inspected = json.loads(run(("docker", "inspect", "--format", "{{json .}}", ids[0])).stdout)
        assert_fixture_container(
            inspected, binding=binding, image=expected_image, name=container_name
        )
        run(("docker", "stop", "--time", "5", inspected["Id"]))
        if run(("docker", "ps", "-aq", "--filter", "id=" + inspected["Id"])).stdout.strip():
            raise BridgeError("owned legacy container not removed")

    failure = None
    after = None
    try:
        write_private_env(
            pg_env,
            {
                "POSTGRES_USER": "postgres",
                "POSTGRES_PASSWORD": admin,
                "POSTGRES_DB": database,
                "OPENCITADEL_MIGRATION_USER": "strict_migration",
                "OPENCITADEL_MIGRATION_PASSWORD": migration,
                "OPENCITADEL_APP_USER": "strict_api",
                "OPENCITADEL_APP_PASSWORD": api,
                "OPENCITADEL_KERNEL_USER": "strict_kernel",
                "OPENCITADEL_KERNEL_PASSWORD": kernel,
            },
        )
        write_private_env(
            phase_env,
            {
                "ENV": "test",
                "POSTGRES_HOST": name,
                "POSTGRES_DB": database,
                "POSTGRES_USER": "strict_kernel",
                "POSTGRES_PASSWORD": kernel,
                "POSTGRES_ADMIN_USER": "strict_migration",
                "POSTGRES_ADMIN_PASSWORD": migration,
                "SQLALCHEMY_DATABASE_URI": f"postgresql+asyncpg://strict_kernel:{kernel}@{name}:5432/{database}",
                "SQLALCHEMY_MIGRATION_DATABASE_URI": f"postgresql+asyncpg://strict_migration:{migration}@{name}:5432/{database}",
                "STRICT_LEGACY_API_URI": f"postgresql+asyncpg://strict_api:{api}@{name}:5432/{database}",
                "DATABASE_AUTHORIZATION_SIGNING_SECRET": signing,
                "API_KEY_SECRET": secrets.token_hex(32),
            },
        )
        obligations.append((name, image))
        atomic_json(
            evidence / "strict-legacy-obligations.json",
            {**input_document, "containers": obligations, "status": "pending"},
        )
        container = run(
            (
                "docker",
                "run",
                "-d",
                "--rm",
                "--name",
                name,
                "--network",
                network_id,
                "--network-alias",
                name,
                *label_args,
                "--tmpfs",
                "/var/lib/postgresql/data:rw,size=536870912",
                "--env-file",
                str(pg_env),
                "--volume",
                f"{root / 'deploy/helm/opencitadel/files/postgres/init-app-role.sh'}:/docker-entrypoint-initdb.d/10-opencitadel-app-role.sh:ro",
                image,
            )
        ).stdout.strip()
        inspected = json.loads(
            run(("docker", "inspect", "--format", "{{json .}}", container)).stdout
        )
        assert_fixture_container(inspected, binding=binding, image=image, name=name)
        legacy["container_id"] = inspected["Id"]
        atomic_json(evidence / "strict-legacy-input.json", input_document)
        deadline = time.monotonic() + 90
        while True:
            result = runner.commands.run(
                (
                    "docker",
                    "exec",
                    container,
                    "pg_isready",
                    "-h",
                    "127.0.0.1",
                    "-U",
                    "postgres",
                    "-d",
                    database,
                ),
                cwd=root,
                timeout=5,
            )
            if result.returncode == 0:
                break
            if time.monotonic() > deadline:
                raise BridgeError("owned legacy database did not become ready")
            time.sleep(0.25)
        for phase in ("old", "current"):
            phase_name = name + "-" + phase
            obligations.append((phase_name, binding["kernel_image"]))
            atomic_json(
                evidence / "strict-legacy-obligations.json",
                {**input_document, "containers": obligations, "status": "pending"},
            )
            args = [
                "docker",
                "run",
                "--rm",
                "--name",
                phase_name,
                "--network",
                network_id,
                *label_args,
                "--env-file",
                str(phase_env),
                "--env",
                "PYTHONPATH=/legacy-api" if phase == "old" else "PYTHONPATH=/app",
                "--volume",
                f"{root / 'scripts/acceptance'}:/acceptance-driver:ro",
                "--volume",
                f"{evidence / 'strict-legacy-input.json'}:/legacy-input.json:ro",
                "--workdir",
                "/legacy-api" if phase == "old" else "/app",
                "--entrypoint",
                "/app/.venv/bin/python",
            ]
            if phase == "old":
                args.extend(("--volume", f"{archived}:/legacy-api:ro"))
            else:
                args.extend(
                    ("--volume", f"{evidence / 'strict-legacy-before.json'}:/legacy-before.json:ro")
                )
            args.extend(
                (
                    binding["kernel_image"],
                    "/acceptance-driver/strict_driver/legacy_phase.py",
                    "--phase",
                    phase,
                )
            )
            output_path = evidence / f"strict-legacy-{phase}-stream.ndjson"
            # Host-owned file survives phase failure/timeout; the producer flushes
            # its pre-rebuild checkpoint before the next fallible operation.
            with output_path.open("x", encoding="utf8") as output:
                os.chmod(output_path, 0o600)
                result = subprocess.run(
                    args,
                    cwd=root,
                    env=runner._environment,
                    stdout=output,
                    stderr=subprocess.DEVNULL,
                    timeout=600,
                    check=False,
                )
            if output_path.stat().st_size > 4 * 1024 * 1024:
                raise BridgeError("legacy phase artifact oversized")
            records = [
                json.loads(line) for line in output_path.read_text().splitlines() if line.strip()
            ]
            for record in records:
                if record.get("kind") == "checkpoint":
                    atomic_json(evidence / "strict-legacy-pre-rebuild.json", record["body"])
            reports = [record["body"] for record in records if record.get("kind") == "report"]
            if result.returncode or len(reports) != 1:
                raise BridgeError("legacy phase failed; raw checkpoint retained")
            report = reports[0]
            if report.get("binding") != binding or report.get("legacy") != legacy:
                raise BridgeError("legacy phase artifact foreign")
            atomic_json(
                evidence
                / ("strict-legacy-before.json" if phase == "old" else "strict-legacy-after.json"),
                report,
            )
            if phase == "current":
                after = report
    except BaseException as exc:
        failure = exc
        raise
    finally:
        failures = []
        for owned_name, owned_image in reversed(obligations):
            try:
                dispose(owned_name, owned_image)
            except BaseException as exc:  # noqa: BLE001 - restore even on cancellation
                failures.append(exc)
        for path in (pg_env, phase_env):
            path.unlink(missing_ok=True)
        atomic_json(
            evidence / "strict-legacy-obligations.json",
            {
                **input_document,
                "containers": obligations,
                "status": "disposed" if not failures else "failed",
                "failure_types": [type(error).__name__ for error in failures],
            },
        )
        if failures:
            raise BaseExceptionGroup(
                "legacy fixture and cleanup failures", ([failure] if failure else []) + failures
            )
    before = read_json(evidence / "strict-legacy-before.json")
    if (
        after is None
        or before["migration"] != "0001greenfield"
        or after["migration"] != binding["migration"]
    ):
        raise BridgeError("legacy actual migration chain mismatch")
    scenarios = []
    for ac, identity, checks in (
        (
            "AC05",
            "missing_history_available_segment",
            {
                "shadow_activated": after["shadow_activated"],
                "explicit_missing_range": bool(
                    after["after"]["run"]["completeness"]["missing_intervals"]
                ),
            },
        ),
        ("AC22", "legacy_coexistence", {"new_events_coexist": after["new_events_coexist"]}),
        (
            "AC22",
            "upcast_hash_replay",
            {
                key: after[key]
                for key in (
                    "old_hashes_unchanged",
                    "old_replay_equal",
                    "upcast_hashes_unchanged",
                    "legacy_missing_fields_unknown",
                    "formal_rebuild_equal",
                )
            },
        ),
        (
            "AC22",
            "old_entrypoint",
            {"old_entrypoint_verified": after["governance"]["chain"]["verified"]},
        ),
    ):
        if any(value is not True for value in checks.values()):
            raise BridgeError("legacy scenario assertion failed")
        scenarios.append(
            {
                "requirement": ac,
                "id": identity,
                "test_id": "strict_driver.legacy_phase." + identity,
                "status": "passed",
                "database": legacy,
                "resource_ids": {
                    "run_id": after["run_id"],
                    "session_id": after["session_id"],
                    "user_id": after["user_id"],
                },
                "before": {
                    "migration": before["migration"],
                    "events": before["event_count"],
                    "status": before["status"],
                },
                "after": {
                    "migration": after["migration"],
                    "events": after["event_count"],
                    "view": after["after"],
                },
                "assertions": [{"id": key, "passed": value} for key, value in checks.items()],
                "fault": {
                    "mechanism": "historical_source_then_forward_migration_separate_database"
                },
                "artifact": "strict-legacy-raw.json",
                "cleanup": {
                    "state": "disposed",
                    "database": database,
                    "container_id": legacy["container_id"],
                },
            }
        )
    raw = {
        "binding": binding,
        "legacy": legacy,
        "scenarios": scenarios,
        "source_archive_sha256": archive_digest,
        "before_sha256": digest(evidence / "strict-legacy-before.json"),
        "after_sha256": digest(evidence / "strict-legacy-after.json"),
    }
    atomic_json(evidence / "strict-legacy-raw.json", raw)
    return scenarios, {
        "path": "strict-legacy-raw.json",
        "sha256": digest(evidence / "strict-legacy-raw.json"),
    }
