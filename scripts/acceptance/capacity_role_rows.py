"""Closed finite public roles produced only by a sealed package session."""

from dataclasses import dataclass, field


class _RoleGuard:
    def __getattribute__(self, name):
        if not name.startswith("_"):
            owner = object.__getattribute__(self, "_session")
            owner._usable()
            if owner._roles.get(object.__getattribute__(self, "_name")) is not self:
                raise ValueError("actual closed public role required")
        return object.__getattribute__(self, name)

    def __eq__(self, other):
        session = object.__getattribute__(self, "_session")
        session._usable()
        name = object.__getattribute__(self, "_name")
        model = session.models[name]
        if type(other) not in (type(self), model):
            return False
        equal = True
        for key in model.model_fields:
            if getattr(self, key) != getattr(other, key):
                equal = False
        return equal

    def model_dump(self):
        session = object.__getattribute__(self, "_session")
        name = object.__getattribute__(self, "_name")
        if name != "environment":
            raise ValueError("whole public role materialization is not supported")
        return session._bounded_role_dump(name)


@dataclass(frozen=True, slots=True, eq=False)
class ProtocolRows(_RoleGuard):
    _session: object = field(repr=False)
    _name: str = field(repr=False)
    schema_version: object
    attempt_id: object
    protocol_id: object
    role: object
    binding_digest: object
    seal_id: object
    registered_ns: object
    clock_id: object
    clock_unit: object
    latency_method: object
    rate_rule: object
    startup_ns: object
    marker_cadence_ns: object
    marker_timeout_ns: object
    marker_count_bound: object
    marker_max_outstanding: object
    load_ready_ns: object
    warm_plan: object
    diagnostics_timing: object
    backend: object
    network_backend: object
    samples: object
    windows: object


@dataclass(frozen=True, slots=True, eq=False)
class MeasurementsRows(_RoleGuard):
    _session: object = field(repr=False)
    _name: str = field(repr=False)
    schema_version: object
    attempt_id: object
    protocol_id: object
    role: object
    native_bindings: object
    markers: object
    live_paints: object
    samples: object
    sources: object
    browsers: object
    resources: object
    errors: object


@dataclass(frozen=True, slots=True, eq=False)
class WorkloadRows(_RoleGuard):
    _session: object = field(repr=False)
    _name: str = field(repr=False)
    schema_version: object
    attempt_id: object
    protocol_id: object
    role: object
    windows: object
    progress: object
    source_acks: object
    errors: object


@dataclass(frozen=True, slots=True, eq=False)
class SealRows(_RoleGuard):
    _session: object = field(repr=False)
    _name: str = field(repr=False)
    schema_version: object
    attempt_id: object
    protocol_id: object
    role: object
    seal_id: object
    fixture_id: object
    fixture_manifest_digest: object
    source_digest: object
    configuration_digest: object
    policy_id: object
    migration: object
    projector_source_version: object
    read_algorithm_version: object
    generation: object
    metric_version: object
    completed_corpus_batch_id: object
    images: object
    cohorts: object


@dataclass(frozen=True, slots=True, eq=False)
class EnvironmentRows(_RoleGuard):
    _session: object = field(repr=False)
    _name: str = field(repr=False)
    schema_version: object
    attempt_id: object
    protocol_id: object
    role: object
    server_host_id: object
    client_host_id: object
    server_cpu_ids: object
    client_cpu_ids: object
    host_memory_bytes: object
    server_memory_bytes: object
    client_memory_bytes: object
    overhead_memory_bytes: object
    postgres_memory_max_bytes: object
    cgroup_id: object
    disk_id: object
    disk_rotational: object
    disk_direct_io: object
    architecture: object
    os: object
    browser: object
    postgres: object
    python: object
    playwright: object
    qemu_binary_sha256: object
    viewport: object
    runtime_limits: object


@dataclass(frozen=True, slots=True, eq=False)
class ResetsRows(_RoleGuard):
    _session: object = field(repr=False)
    _name: str = field(repr=False)
    schema_version: object
    attempt_id: object
    protocol_id: object
    role: object
    resets: object


@dataclass(frozen=True, slots=True, eq=False)
class NetworkRows(_RoleGuard):
    _session: object = field(repr=False)
    _name: str = field(repr=False)
    schema_version: object
    attempt_id: object
    protocol_id: object
    role: object
    backend: object
    host_link: object
    client_link: object
    forward_bound_address: object
    guest_ports: object
    global_ip_forward_changed: object
    nat_changed: object
    cdp_delay_ms: object
    context_ids: object
    validity: object
    calibrations: object
    phase_intervals: object


@dataclass(frozen=True, slots=True, eq=False)
class DiagnosticsRows(_RoleGuard):
    _session: object = field(repr=False)
    _name: str = field(repr=False)
    schema_version: object
    attempt_id: object
    protocol_id: object
    role: object
    queries: object


@dataclass(frozen=True, slots=True, eq=False)
class CleanupRows(_RoleGuard):
    _session: object = field(repr=False)
    _name: str = field(repr=False)
    schema_version: object
    attempt_id: object
    protocol_id: object
    rounds: object
    role: object
    cohorts: object
    total: object
    dispositions: object
    pending: object
    quarantined: object
    status: object


@dataclass(frozen=True, slots=True, eq=False)
class C2CRows(_RoleGuard):
    _session: object = field(repr=False)
    _name: str = field(repr=False)
    schema_version: object
    role: object
    attempt_id: object
    protocol_id: object
    projection_version: object
    units: object


@dataclass(frozen=True, slots=True, eq=False)
class FixtureRows(_RoleGuard):
    _session: object = field(repr=False)
    _name: str = field(repr=False)
    schema_version: object
    fixture_id: object
    seed: object
    scope_ids: object
    counts: object
    window_start: object
    window_end: object
    status: object
    target: object
    ownership_journal: object


ROLE_ROWS = {
    "protocol": ProtocolRows,
    "measurements": MeasurementsRows,
    "workload": WorkloadRows,
    "seal": SealRows,
    "environment": EnvironmentRows,
    "resets": ResetsRows,
    "network": NetworkRows,
    "diagnostics": DiagnosticsRows,
    "cleanup": CleanupRows,
    "c2c": C2CRows,
    "fixture": FixtureRows,
}
