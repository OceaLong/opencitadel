"""Independent observer clients: no kernel, writer journal, provisioning or DDL."""

import time
from contextlib import asynccontextmanager
from ipaddress import IPv4Address
from types import SimpleNamespace
from urllib.parse import urlsplit

from scripts.execution_capacity.evidence_objects import EvidenceObjects
from scripts.execution_capacity.evidence_owner import EvidenceOwner
from scripts.execution_capacity.evidence_transport import EvidenceTransport
from scripts.execution_capacity.observer_session import ObserverSession
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.infrastructure.adapters.object_storage import MinioObjectStorageAdapter
from app.infrastructure.storage.minio import Minio


def owned_minio_endpoint(binding, rows):
    """Translate the authenticated original alias only through its owned network."""
    key = binding["minio_container"]
    row = rows[key]
    networks = row["NetworkSettings"]["Networks"]
    if (
        row["Id"] != key
        or binding["containers"][key]["service"] != "minio"
        or len(networks) != 1
        or next(iter(networks.values()))["NetworkID"] != binding["network_id"]
        or row["State"]["Running"] is not True
    ):
        raise ValueError("observer MinIO owned container/network differs")
    network = next(iter(networks.values()))
    endpoint = urlsplit("//" + binding["minio_endpoint"])
    aliases = set(network.get("Aliases") or ()) | {row["Name"].removeprefix("/")}
    if (
        endpoint.hostname not in aliases
        or endpoint.port != 9000
        or endpoint.username
        or endpoint.password
        or endpoint.path
        or endpoint.query
        or endpoint.fragment
        or "9000/tcp" not in row["Config"].get("ExposedPorts", {})
    ):
        raise ValueError("observer MinIO original alias binding differs")
    address = str(IPv4Address(network["IPAddress"]))
    for other_key, other in rows.items():
        if other_key == key:
            continue
        for other_network in other["NetworkSettings"]["Networks"].values():
            if other_network["NetworkID"] == binding["network_id"] and (
                endpoint.hostname in (other_network.get("Aliases") or ())
                or endpoint.hostname == other["Name"].removeprefix("/")
                or address == other_network["IPAddress"]
            ):
                raise ValueError("observer MinIO alias/address is ambiguous")
    return f"{address}:9000"


@asynccontextmanager
async def open_observers(settings, binding, *, owned_containers=None, evidence_owner=None):
    if type(evidence_owner) is not EvidenceOwner or evidence_owner.journal is None:
        raise ValueError("actual bounded observer evidence owner required")
    evidence_owner.journal._usable(writing=True)
    endpoint = (
        binding["minio_endpoint"]
        if owned_containers is None
        else owned_minio_endpoint(binding, owned_containers)
    )
    if (
        settings.env != "test"
        or binding["environment"] != "test"
        or settings.storage_provider != "minio"
        or settings.minio_endpoint != endpoint
        or settings.minio_bucket != binding["minio_bucket"]
    ):
        raise ValueError("observer target differs from dedicated binding")
    # Every service-created UOW inherits readonly connection options, not merely
    # the explicit inventory snapshot. Never call normal Postgres.init's DDL.
    engine = create_async_engine(
        settings.sqlalchemy_database_uri,
        pool_pre_ping=True,
        execution_options={"postgresql_readonly": True, "isolation_level": "REPEATABLE READ"},
    )
    evidence = evidence_owner
    sessions = async_sessionmaker(
        engine,
        autoflush=False,
        sync_session_class=ObserverSession,
        budget=evidence.budget,
        evidence=evidence,
        info={
            "database_authorization_signing_secret": settings.database_authorization_signing_secret
        },
    )
    storage = Minio(settings, real_test_io=True)
    resources = SimpleNamespace(
        settings=settings,
        evidence=evidence,
        evidence_transport=EvidenceTransport(evidence.budget.child(), evidence=evidence),
        evidence_objects=EvidenceObjects(
            MinioObjectStorageAdapter(storage), evidence.budget.child(), evidence=evidence
        ),
        postgres=SimpleNamespace(session_factory=sessions),
        object_storage_client=storage,
        opened_ns=time.monotonic_ns(),
        closed_ns=None,
    )
    primary = None
    try:
        await storage.init()  # verifies existing bucket; explicit real-test mode never provisions
        yield resources
    except BaseException as error:  # noqa: BLE001 - preserve primary alongside all close failures
        primary = error
    finally:
        failures = []
        for close in (storage.shutdown, engine.dispose):
            try:
                await close()
            except BaseException as error:  # noqa: BLE001 - retain independent cleanup failures
                failures.append(error)
        if not failures:
            resources.closed_ns = time.monotonic_ns()
        if failures:
            raise BaseExceptionGroup(
                "observer operation and closure failures", ([primary] if primary else []) + failures
            )
        if primary is not None:
            raise primary
