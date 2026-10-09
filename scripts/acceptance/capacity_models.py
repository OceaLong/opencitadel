"""Version 3 safe export contract, shared by collector and consumer.

These are allowlisted observations, not signed physical attestations. Private
journal payloads, recovery keys, credentials and disk bytes are never report roles.
All timestamps explicitly name their clock; no host/guest epoch subtraction.
"""

from typing import Annotated, Literal

from pydantic import (
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)
from scripts.acceptance.capacity_c2c_models import C2c
from scripts.acceptance.capacity_diagnostics import Query
from scripts.acceptance.capacity_records import ID, Digest, Nat, Number, Pos, Record

Dimension = Literal["standard", "step_capacity", "admission"]
Mode = Literal["warm", "cold", "baseline", "loaded"]
Operation = Literal[
    "first_screen", "switch", "history", "analysis", "matrix", "live_visible", "admission"
]


class Role(Record):
    schema_version: Literal[3] = 3
    attempt_id: ID
    protocol_id: ID


class Target(Record):
    scope_id: ID
    run_id: ID | None
    # Opaque public cut/filter/batch and revision only; no private recovery IDs.
    public_id: ID
    revision: ID
    step_id: ID | None


NativeNumber = Annotated[StrictFloat, Field(ge=0, allow_inf_nan=False)]


class NativeFilters(Record):
    start: ID
    end: ID
    timezone: ID
    grain: Literal["hour", "day"]
    # Closed public filter set; no retained capture or executable/URL selector.
    family: ID | None = None
    configuration_revision: ID | None = None
    mode: ID | None = None
    purpose: ID | None = None


class NativeHistory(Record):
    direction: Literal["previous", "next", "time"]
    anchor_at: ID
    target_time: ID | None = None


class NativeIntent(Record):
    operation: Literal["first_screen", "switch", "history", "analysis", "matrix", "live_visible"]
    scope_id: ID
    session_id: ID | None
    run_id: ID | None
    step_id: ID | None
    batch_id: ID | None
    view: Literal["task", "debug"]
    history: NativeHistory | None
    filters: NativeFilters | None
    binding_rule: Literal["first-authenticated-causal-response-v1"] = (
        "first-authenticated-causal-response-v1"
    )

    @model_validator(mode="after")
    def operation_shape(self):
        if self.operation == "analysis":
            if self.filters is None or any(
                (self.session_id, self.run_id, self.step_id, self.batch_id, self.history)
            ):
                raise ValueError("fresh analysis requires only its exact filters")
        elif self.operation == "matrix":
            if not self.batch_id or any(
                (self.session_id, self.run_id, self.step_id, self.filters, self.history)
            ):
                raise ValueError("matrix requires actual completed batch")
        elif not self.session_id or not self.run_id or self.batch_id or self.filters:
            raise ValueError("execution intent requires observed cohort/session Run")
        if (self.operation == "history") != (self.history is not None):
            raise ValueError("history selector mismatch")
        if self.operation in {"switch", "live_visible"} and not self.step_id:
            raise ValueError("step operation requires known public step")
        return self


NativeNS = Annotated[StrictStr, Field(pattern=r"^(0|[1-9][0-9]{0,19})$")]


class NativeBinding(Record):
    sample_id: ID
    action_id: ID
    page_id: ID
    context_id: ID
    clock_id: ID
    request_id: ID
    response_id: ID
    request_ns: NativeNS
    received_ns: NativeNS
    target: Target
    session_id: ID | None
    batch_id: ID | None


# Wire timestamps use decimal strings: JS Number cannot carry arbitrary host ns.
NativeText = Annotated[StrictStr, Field(max_length=4096)]


class NativeCommand(Record):
    wire_version: Literal[1]
    command: Literal["collect"]
    mode: Literal["warm", "cold"]
    attempt_id: ID
    protocol_id: ID
    sample_id: ID
    action_id: ID
    context_id: ID
    page_id: ID
    window_id: ID
    clock_id: ID
    intent: NativeIntent
    deadline_ns: NativeNS
    window_open_ns: NativeNS | None
    resource_offset_ns: NativeNS
    resource_duration_ns: NativeNS
    require_positive_scroll: StrictBool
    # All counts include setup/tail observations; overflow invalidates, never samples.
    max_captures: Literal[121]
    viewport_width: Literal[1440]
    viewport_height: Literal[900]
    collector_feature: Literal["CDPScreenshotNewSurface"]

    @model_validator(mode="after")
    def initial_open(self):
        if (self.intent.operation == "live_visible") != (self.window_open_ns is None):
            raise ValueError("live open must arrive as actual later control")
        return self


class NativeOwner(Record):
    serial: Pos
    content: ID
    text: NativeText
    identifier: ID
    run_id: ID | None
    step_id: ID | None
    revision: ID | None
    result_id: ID | None
    rect: Annotated[list[NativeNumber], Field(min_length=4, max_length=4)]
    native_rect: Annotated[list[NativeNumber], Field(min_length=4, max_length=4)]
    text_rects: Annotated[
        list[Annotated[list[NativeNumber], Field(min_length=4, max_length=4)]],
        Field(min_length=1, max_length=32),
    ]
    render_time_ms: NativeNumber


class NativeReadback(Record):
    kind: Literal["readback"]
    target: Target
    document_id: ID
    frame_id: ID
    execution_context_id: ID
    target_id: ID
    mutation_sequence: Nat
    owners: Annotated[list[NativeOwner], Field(min_length=1, max_length=256)]
    visible: Literal[True]


class NativePostcheck(NativeReadback):
    kind: Literal["postcheck"]
    capture_id: ID
    readback_sequence: Pos


class NativeImage(Record):
    kind: Literal["image"]
    capture_id: ID
    chunk_index: Nat
    # <= 48 KiB raw chunks, retained privately, never a public summary field.
    data: Annotated[
        StrictStr, Field(min_length=4, max_length=65536, pattern=r"^[A-Za-z0-9+/]+={0,2}$")
    ]


class NativeProcess(Record):
    pid: Pos
    start: ID


class NativeCapture(Record):
    kind: Literal["capture"]
    capture_id: ID
    request_id: ID
    target_id: ID
    readback_sequence: Pos
    renderer_candidates: Annotated[list[NativeProcess], Field(min_length=1, max_length=128)]
    dispatched_ns: NativeNS
    received_ns: NativeNS
    postcheck_ns: NativeNS
    sha256: Digest
    bytes: Annotated[StrictInt, Field(gt=0, le=8388608)]
    chunks: Annotated[StrictInt, Field(gt=0, le=171)]
    width: Literal[1440]
    height: Literal[900]
    # Capability observations never become acceptance by themselves.
    qualification: Literal["pending-runtime-qualification", "matched-reviewed-build"]
    retention: Literal["full", "progress-region"]
    retained_sha256: Digest
    retained_bytes: Annotated[StrictInt, Field(gt=0, le=8388608)]
    crop_rect: Annotated[list[Nat], Field(min_length=4, max_length=4)] | None
    transform: Literal["png-native-full-v1", "png-lossless-text-hull-pad4-v1"]
    channels: Literal[3, 4]

    @model_validator(mode="after")
    def retained_geometry(self):
        if not int(self.dispatched_ns) <= int(self.received_ns) <= int(self.postcheck_ns):
            raise ValueError("native receipt ordering")
        if self.chunks != (self.retained_bytes + 49151) // 49152:
            raise ValueError("native retained chunk count")
        if self.retention == "full":
            if (
                self.crop_rect is not None
                or self.transform != "png-native-full-v1"
                or self.retained_sha256 != self.sha256
                or self.retained_bytes != self.bytes
            ):
                raise ValueError("full original retention mismatch")
        else:
            if (
                self.crop_rect is None
                or self.transform != "png-lossless-text-hull-pad4-v1"
                or self.retained_bytes > 98304
                or self.qualification != "matched-reviewed-build"
            ):
                raise ValueError("progress crop provenance")
            x, y, width, height = self.crop_rect
            if not (
                0 < width <= 320 and 0 < height <= 64 and x + width <= 1440 and y + height <= 900
            ):
                raise ValueError("progress crop geometry")
        return self


class NativeTraceCompletion(Record):
    kind: Literal["trace-completion"]
    artifact_id: Literal["native-trace"]
    stream_id: ID | None
    end_dispatched_ns: NativeNS
    end_received_ns: NativeNS | None
    complete_received_ns: NativeNS | None
    eof_received_ns: NativeNS | None
    data_loss: StrictBool | None
    parser: Literal["complete", "failed", "incomplete"]
    observed_bytes: Nat
    retained_bytes: Nat
    retained_chunks: Nat
    retained_sha256: Digest

    @model_validator(mode="after")
    def observed_order(self):
        ordered = [
            self.end_dispatched_ns,
            self.end_received_ns,
            self.complete_received_ns,
            self.eof_received_ns,
        ]
        present = [int(n) for n in ordered if n is not None]
        if (
            any(n < int(self.end_dispatched_ns) for n in present)
            or (
                self.eof_received_ns is not None
                and self.complete_received_ns is not None
                and int(self.eof_received_ns) < int(self.complete_received_ns)
            )
            or self.retained_bytes > self.observed_bytes
        ):
            raise ValueError("native trace receipt order/totals")
        if self.parser == "complete" and (
            self.eof_received_ns is None
            or self.stream_id is None
            or self.complete_received_ns is None
        ):
            raise ValueError("native trace incomplete receipt")
        return self


class NativeAction(Record):
    kind: Literal["action"]
    operation: Literal["first_screen", "switch", "history", "analysis", "matrix", "live_visible"]
    trigger_ns: NativeNS


class NativeOpen(Record):
    kind: Literal["open"]
    control_sequence: Pos
    source_receipt_id: ID
    opened_ns: NativeNS
    received_ns: NativeNS


class NativeResourceBoundary(Record):
    kind: Literal["resource-boundary"]
    phase: Literal["begin", "end"]
    observed_ns: NativeNS


class NativeResource(Record):
    kind: Literal["resource"]
    phase: Literal["preopen", "capture", "tail"]
    document_id: ID
    frame_id: ID
    execution_context_id: ID
    local_ms: NativeNumber
    host_before_ns: NativeNS
    host_after_ns: NativeNS
    frame_intervals_ms: Annotated[list[NativeNumber], Field(max_length=128)]
    frame_times_ms: Annotated[list[NativeNumber], Field(max_length=128)]
    long_tasks: Annotated[
        list[Annotated[list[NativeNumber], Field(min_length=2, max_length=2)]],
        Field(max_length=128),
    ]
    heap_bytes: Nat | None
    mounted_rows: Nat | None
    visible: StrictBool
    observer_lost: StrictBool


class NativeScroll(Record):
    kind: Literal["scroll"]
    before: Annotated[list[NativeNumber], Field(min_length=3, max_length=3)]
    after: Annotated[list[NativeNumber], Field(min_length=3, max_length=3)]
    reason: Literal["realized-native-wheel", "observed-nonoverflow"]


class NativeReady(Record):
    kind: Literal["ready"]
    principal_id: ID
    scope_id: ID
    run_id: ID | None
    target_id: ID
    frame_id: ID
    subscription_request_id: ID | None
    subscription_event_seen: StrictBool
    subscription_event_id: ID | None
    subscription_cursor: ID | None
    subscription_started_ns: NativeNS | None
    subscription_response_ns: NativeNS | None
    observer_installed_ns: NativeNS


class NativeFailure(Record):
    kind: Literal["error"]
    code: Literal[
        "invalid-command",
        "late-resource-open",
        "response-binding",
        "not-ready",
        "hidden-content",
        "unstable-content",
        "missing-element-timing",
        "observer-loss",
        "unsupported-native-build",
        "renderer-binding",
        "capture-failed",
        "deadline",
        "transport",
        "cancelled",
        "resource-clock",
        "progress-mapping",
    ]
    stage: ID
    request_id: ID | None
    # Never raw exceptions/response bodies/headers (may contain credentials).
    evidence_digest: Digest | None


class NativePublicResponse(Record):
    kind: Literal["public-response"]
    binding: NativeBinding


class NativeMapping(Record):
    kind: Literal["progress-mapping"]
    capture_id: ID
    progress_ids: Annotated[list[ID], Field(min_length=1, max_length=120)]
    painted_progress_id: ID
    step_id: ID
    revision: Pos
    public_message: ID


class NativePreparation(Record):
    kind: Literal["preparation"]
    started_ns: NativeNS
    completed_ns: NativeNS
    run_id: ID
    at: ID
    revision: ID


class NativePrivateChunk(Record):
    kind: Literal["private-chunk"]
    artifact_id: ID
    purpose: Literal["native-trace", "rejected-capture"]
    chunk_index: Nat
    data: Annotated[
        StrictStr, Field(min_length=4, max_length=65536, pattern=r"^[A-Za-z0-9+/]+={0,2}$")
    ]


class NativeStatus(Record):
    kind: Literal["closed"]
    outcome: Literal["observations-closed", "failed"]
    records: Nat


class NativeCapability(Record):
    kind: Literal["capability"]
    product: ID
    revision: ID
    protocol_version: ID
    executable_sha256: Digest | None
    feature_enabled: StrictBool
    feature_conflict: StrictBool
    platform: ID
    browser_pid: Pos | None
    browser_start: ID | None
    renderer_pid: Pos | None
    renderer_start: ID | None
    frame_id: ID
    source_qualification_digest: Digest | None


class NativeBindingObservation(Record):
    kind: Literal["binding"]
    binding: NativeBinding


class NativePendingOperation(Record):
    operation_id: ID
    sample_id: ID
    action_id: ID
    stage: ID
    started_ns: NativeNS
    settled_ns: NativeNS | None
    state: Literal["pending", "fulfilled", "rejected"]
    late: StrictBool
    error_digest: Digest | None = None


class NativeRetainedArtifact(Record):
    artifact_id: ID
    observed_bytes: Nat
    retained_bytes: Annotated[StrictInt, Field(ge=0, le=134217728)]
    acknowledged_bytes: Nat
    sha256: Digest
    received_ns: NativeNS


class NativeFailureSourceRecordRef(Record):
    kind: Literal["native-record"]
    sequence: Annotated[Pos, Field(le=65536)]
    purpose: Literal["rejected-capture", "native-trace"]
    artifact_id: ID
    chunk_index: Nat
    artifact_offset: Annotated[Nat, Field(le=134217728)]
    bytes: Annotated[Pos, Field(le=49152)]


class NativeRetainedArtifactV2(NativeRetainedArtifact):
    retained_bytes: Annotated[StrictInt, Field(gt=0, le=134217728)]
    source_record_refs: Annotated[list[NativeFailureSourceRecordRef], Field(max_length=4096)]


class NativeLateHandle(Record):
    operation_id: ID
    kind: Literal["new-context", "new-page", "new-session", "browser-session"]


class NativeFailureSnapshot(Record):
    wire_version: Literal[1]
    attempt_id: ID
    protocol_id: ID
    sample_id: ID
    action_id: ID
    context_id: ID
    page_id: ID
    window_id: ID
    clock_id: ID
    observed_ns: NativeNS
    failure_ns: NativeNS | None
    retention_deadline_ns: NativeNS | None
    cause: ID | None
    discarded_bytes: Nat
    held_bytes: Annotated[StrictInt, Field(ge=0, le=134217728)]
    operations: Annotated[list[NativePendingOperation], Field(max_length=128)]
    handles: Annotated[list[NativeLateHandle], Field(max_length=128)]
    artifacts: Annotated[list[NativeRetainedArtifact], Field(max_length=128)]
    disposition: Literal["pending", "partial", "settled"]


class NativeFailureSnapshotV2(NativeFailureSnapshot):
    wire_version: Literal[2]
    failure_ns: NativeNS
    retention_deadline_ns: NativeNS
    cause: ID
    artifacts: Annotated[list[NativeRetainedArtifactV2], Field(max_length=128)]


class NativeRecord(Record):
    wire_version: Literal[1]
    attempt_id: ID
    protocol_id: ID
    sample_id: ID
    action_id: ID
    context_id: ID
    page_id: ID
    window_id: ID
    clock_id: ID
    sequence: Pos
    received_ns: NativeNS
    observation: Annotated[
        NativeReady
        | NativeBindingObservation
        | NativeReadback
        | NativePostcheck
        | NativeImage
        | NativeCapture
        | NativeResource
        | NativeResourceBoundary
        | NativeOpen
        | NativeTraceCompletion
        | NativeAction
        | NativeScroll
        | NativeCapability
        | NativeFailure
        | NativeStatus
        | NativePreparation
        | NativePrivateChunk
        | NativePublicResponse
        | NativeMapping,
        Field(discriminator="kind"),
    ]


class LiveResourceInterval(Record):
    # A positive offset leaves time to start actual capture after host-open.
    offset_ns: Pos
    duration_ns: Pos


class Plan(Record):
    sample_id: ID
    dimension: Dimension
    mode: Mode
    operation: Operation
    ordinal: Nat
    target: Target | None
    intent: NativeIntent | None = None
    window_id: ID | None
    physical_window_id: ID
    reset_id: ID | None
    prewarm_completed_ns: Nat | None
    action_id: ID
    page_id: ID
    context_id: ID
    live_resource_interval: LiveResourceInterval | None = None
    require_positive_scroll: StrictBool = False

    @model_validator(mode="after")
    def resource_rule(self):
        if (self.target is None) == (self.intent is None):
            raise ValueError("exactly one retained target or first-issued intent required")
        if self.intent and self.intent.operation != self.operation:
            raise ValueError("operation/intent mismatch")
        if self.mode == "baseline":
            if self.window_id is not None:
                raise ValueError("baseline physical round cannot claim combined load")
        elif self.physical_window_id != self.window_id:
            raise ValueError("loaded physical window differs from semantic window")
        if (self.operation == "live_visible") != (self.live_resource_interval is not None):
            raise ValueError("live resource interval must match immutable operation")
        return self


class MeasurementInterval(Record):
    start_offset_ns: Nat
    end_offset_ns: Pos
    control_margin_ns: Pos

    @model_validator(mode="after")
    def ordered(self):
        if self.start_offset_ns >= self.end_offset_ns:
            raise ValueError("measurement interval must be nonempty")
        return self

    def phase(self, offset_ns):
        if offset_ns < 0:
            return "setup"
        if offset_ns < self.start_offset_ns:
            return "premeasurement"
        return "measured" if offset_ns < self.end_offset_ns else "tail"


class CalibrationSlot(Record):
    offset_ns: Nat
    budget_ns: Pos


class WindowPlan(Record):
    window_id: ID
    startup_ns: Pos
    marker_count: Pos
    marker_anchor: Literal["first-minimal-ready-receipt"]
    trigger: Literal["guest-open-receipt"]
    seconds: Annotated[StrictInt, Field(ge=2, le=30)]
    session_ids: list[ID]
    measurement: MeasurementInterval
    calibration_window: CalibrationSlot

    @model_validator(mode="after")
    def measurement_guard(self):
        if (
            self.calibration_window.offset_ns + self.calibration_window.budget_ns
            > self.measurement.end_offset_ns
        ):
            raise ValueError("calibration slot exceeds immutable measured interval")
        if (
            self.measurement.end_offset_ns + 2_000_000_000 + self.measurement.control_margin_ns
            > self.seconds * 1_000_000_000
        ):
            raise ValueError(
                "measurement tail guard cannot cover original 2s budget and control margin"
            )
        return self


class Protocol(Role):
    role: Literal["protocol"] = "protocol"
    binding_digest: Digest
    seal_id: ID
    registered_ns: Nat
    clock_id: ID
    clock_unit: Literal["nanoseconds"]
    latency_method: Literal["coordinator_predispatch_to_paint_upper_bound"]
    rate_rule: Literal["each-1s-bin-at-least-2-v1"]
    startup_ns: Pos
    marker_cadence_ns: Pos
    marker_timeout_ns: Pos
    marker_count_bound: Pos
    marker_max_outstanding: Literal[1]
    load_ready_ns: Literal[2000000000]
    warm_plan: Literal["same-target-explicit-prewarm-before-window"]
    diagnostics_timing: Literal["after-timed-read-or-separate-clone"]
    backend: Literal["linux-x86_64-kvm-qemu"]
    network_backend: Literal["owned-netns-veth-qemu-usernet"]
    samples: list[Plan]
    windows: list[WindowPlan]

    @model_validator(mode="after")
    def unique_physical_samples(self):
        if len({p.physical_window_id for p in self.samples}) != len(self.samples):
            raise ValueError("physical window must be unique per preregistered sample")
        return self


class Sample(Record):
    sample_id: ID
    clock_id: ID
    start_ns: Nat
    end_ns: Nat
    status: Literal["ok", "error"]
    error: ID | None
    action_id: ID
    # Earliest navigation/fetch/click. End is coordinator observation of native paint.
    trigger_ns: Nat
    source_id: ID
    browser_id: ID | None


class Source(Record):
    source_id: ID
    sample_id: ID
    target: Target
    clock_id: ID
    submitted_ns: Nat
    acknowledged_ns: Nat
    readback_ns: Nat
    kind: Literal[
        "interactive_summary",
        "selected_step",
        "history_revision",
        "analysis_capture",
        "matrix_page",
        "progress",
        "formal_admission",
    ]
    public_event_id: ID | None
    sequence: Nat | None
    progress_id: ID | None
    marker_id: ID | None
    captured_runs: Nat
    matrix_results: Nat
    # Formal admission uses actual AgentService attached Run, not HTTP acceptance.
    admission_session_id: ID | None
    admission_run_id: ID | None
    profile: Literal["acceptance-capacity", "finite-text-120x500ms-v1"]
    policy_id: ID

    # Original repository execution, transferred through the verified C3 source
    # channel. Missing authority stays invalid at the diagnostics join.
    repository_capture_digest: Digest | None = None
    repository_clock_id: ID | None = None
    database_identity_digest: Digest | None = None
    build_inventory_digest: Digest | None = None


class Browser(Record):
    browser_id: ID
    sample_id: ID
    action_id: ID
    page_id: ID
    context_id: ID
    clock_id: ID
    readback_target: Target
    painted_target: Target
    public_event_id: ID | None
    sequence: Nat | None
    readback_observed_ns: Nat
    paint_observed_ns: Nat
    completion: Literal[
        "summary_interactive",
        "selected_step_painted",
        "history_painted",
        "analysis_displayed",
        "matrix_usable",
        "progress_painted",
    ]
    visible: StrictBool


class Frame(Record):
    observed_ns: Nat
    interval_ms: Number


class HeapSample(Record):
    observed_ns: Nat
    bytes: Nat


class DOMSample(Record):
    observed_ns: Nat
    rows: Nat


class LongTask(Record):
    start_ns: Nat
    end_ns: Nat


class ResourceTrace(Record):
    sample_id: ID
    clock_id: ID
    context_id: ID
    renderer_id: ID
    start_ns: Nat
    end_ns: Nat
    visible: StrictBool
    forced_gc: StrictBool
    scroll_start_ns: Nat
    scroll_end_ns: Nat
    scroll_distance_px: Nat
    scroll_nonoverflow: StrictBool = False
    scroll_height_px: Nat = 0
    scroll_client_height_px: Nat = 0
    frames: list[Frame]
    heap_samples: list[HeapSample]
    dom_samples: list[DOMSample]
    long_tasks: list[LongTask]


class Marker(Record):
    marker_id: ID
    window_id: ID
    boot_id: ID
    clock_id: ID
    sequence: Pos
    scheduled_ns: Nat
    sent_ns: Nat
    completed_ns: Nat
    installed_ack_ns: Nat
    guest_installed_ns: Nat
    command_nonce: ID
    response_nonce: ID


class LivePaint(Record):
    progress_id: ID
    marker_id: ID
    clock_id: ID
    context_id: ID
    event_id: ID
    run_id: ID
    sequence: Pos
    projection_revision: Pos
    source_ack_received_ns: Nat
    public_readback_received_ns: Nat
    paint_received_ns: Nat
    visible: StrictBool


class Measurements(Role):
    role: Literal["measurements"] = "measurements"
    native_bindings: list[NativeBinding] = []  # noqa: RUF012 - Pydantic copies the existing field default
    markers: list[Marker]
    live_paints: list[LivePaint]
    samples: list[Sample]
    sources: list[Source]
    browsers: list[Browser]
    resources: list[ResourceTrace]
    errors: list[ID]


class Claim(Record):
    run_id: ID
    activity_id: ID
    generation: Nat
    claim_generation: Pos
    call_identity: ID
    session_id: ID
    policy_id: ID
    configured_model: Literal["acceptance-live"]
    stream: StrictBool
    boot_id: ID


class ClaimObservation(Record):
    run_id: ID
    activity_id: ID
    generation: Nat
    claim_generation: Pos
    call_identity: ID
    policy_id: ID
    reservation_id: ID
    claimed_by: ID
    # ISO8601 SQL timestamptz facts; never mapped onto guest monotonic time.
    sql_observed_at: ID
    call_started_at: ID
    heartbeat_at: ID
    claim_deadline: ID
    timeout_at: ID
    lease_live: StrictBool
    settled: StrictBool
    terminal: StrictBool
    reservation_state: ID
    status: ID
    configured_model: ID
    stream: StrictBool


class ClaimSnapshot(Record):
    before_ns: Nat
    after_ns: Nat
    boot_id: ID
    claims: list[ClaimObservation]


class Progress(Record):
    phase: Literal["setup", "premeasurement", "measured", "tail"]
    progress_id: ID
    window_id: ID
    run_id: ID
    activity_id: ID
    generation: Nat
    claim_generation: Pos
    boot_id: ID
    pid: Pos
    marker_id: ID
    marker_sequence: Pos
    marker_captured_ns: Nat
    event_id: ID
    sequence: Pos
    before_ns: Nat
    after_ns: Nat
    ack: StrictBool
    error: ID | None
    message: ID
    # Safe exact extraction of existing observation/public-event SQL readback.
    source_identity: ID
    source_activity_id: ID
    source_generation: Nat
    source_claim_generation: Pos
    source_sequence: Pos
    applied: StrictBool
    observed_order: Pos
    projection_revision: Pos
    public_event_id: ID
    public_run_id: ID
    public_message: ID


class JoinedProgress(Record):
    progress: Progress
    query_before_ns: Nat
    query_after_ns: Nat


class ProgressPage(Record):
    schema_version: Literal[3] = 3
    window_id: ID
    boot_id: ID
    cursor: Nat
    next_cursor: Nat
    total: Nat
    rows: list[JoinedProgress]
    digest: Digest


class SourceAck(Record):
    progress_id: ID
    clock_id: ID
    received_ns: Nat
    progress_digest: Digest
    query_before_ns: Nat
    query_after_ns: Nat


class BatchTick(Record):
    before_ns: Nat
    after_ns: Nat
    status: Literal["running"]
    subject_concurrency: Pos
    judge_concurrency: Pos
    environment_concurrency: Pos
    sends: Nat
    settled: Nat
    active_call_ids: list[ID]


class RoundOrigin(Record):
    schema_version: Literal[1]
    parent_attempt_id: ID
    round_id: ID
    sample_id: ID
    window_id: ID
    parent_plan_digest: Digest
    child_plan_digest: Digest
    reservation_digest: Digest
    child_origin_sha256: Digest


class Window(Record):
    round_origin: RoundOrigin
    window_id: ID
    boot_id: ID
    guest_minimal_ready_ns: Nat
    guest_start_ns: Nat
    guest_end_ns: Nat
    guest_cohort_ns: Nat
    guest_ready_ns: Nat
    guest_done_ns: Nat
    guest_measurement_closed_ns: Nat
    host_measurement_closed_received_ns: Nat
    host_metadata_received_ns: Nat
    host_cohort_received_ns: Nat
    host_ready_sent_ns: Nat
    host_done_sent_ns: Nat
    host_done_received_ns: Nat
    native_ready_digest: Digest
    native_done_digest: Digest
    snapshots: list[ClaimSnapshot]
    coordinator_start_ns: Nat
    coordinator_end_ns: Nat
    clock_id: ID
    context_ids: list[ID]
    session_ids: list[ID]
    claims: list[Claim]
    batch_id: ID
    suite_version: ID
    batch_results: Literal[5000]
    ticks: list[BatchTick]


class GuestWindowSource(Record):
    attempt_id: ID
    measurement: MeasurementInterval
    window_id: ID
    boot_id: ID
    source_digest: Digest
    minimal_ready_ns: Nat
    start_ns: Nat
    end_ns: Nat
    cohort_ns: Nat
    ready_ns: Nat
    done_ns: Nat
    measurement_closed_ns: Nat
    cohort_digest: Digest
    native_ready_digest: Digest
    native_done_digest: Digest
    session_ids: list[ID]
    claims: list[Claim]
    batch_id: ID
    suite_version: ID
    batch_results: Literal[5000]
    cleanup: Literal["pending_C"]


class SourceShard(Record):
    schema_version: Literal[3] = 3
    window_id: ID
    boot_id: ID
    kind: Literal["metadata", "snapshots", "ticks", "progress"]
    index: Nat
    count: Pos
    total: Pos
    rows: list[GuestWindowSource | ClaimSnapshot | BatchTick | Progress]
    digest: Digest

    @model_validator(mode="after")
    def kind_matches(self):
        expected = {
            "metadata": GuestWindowSource,
            "snapshots": ClaimSnapshot,
            "ticks": BatchTick,
            "progress": Progress,
        }[self.kind]
        if not self.rows or any(not isinstance(row, expected) for row in self.rows):
            raise ValueError("source shard kind/schema mismatch")
        return self


class Workload(Role):
    role: Literal["workload"] = "workload"
    windows: list[Window]
    progress: list[Progress]
    source_acks: list[SourceAck]
    errors: list[ID]


class Counts(Record):
    runs: Nat
    formal_events: Nat
    observations: Nat
    visible_steps: Nat


class SourceOrigin(Record):
    kind: Literal["base", "round"]
    seal_id: ID
    round: RoundOrigin | None
    boot_id: ID | None
    clone_id: ID | None

    @model_validator(mode="after")
    def complete_origin(self):
        if self.kind == "base":
            if any(x is not None for x in (self.round, self.boot_id, self.clone_id)):
                raise ValueError("base source cannot claim a later physical origin")
        elif any(x is None for x in (self.round, self.boot_id, self.clone_id)):
            raise ValueError("round source requires actual parent/child/boot/clone")
        return self


class Cohort(Record):
    origin: SourceOrigin
    cohort_id: ID
    kind: Literal[
        "standard", "step_capacity", "evaluation_subject", "evaluation_judge", "live", "admission"
    ]
    scope_id: ID
    run_ids: list[ID]
    source: Counts
    view: Counts
    parity_digest: Digest


class RoundInventory(Record):
    origin: SourceOrigin
    base_inventory_digest: Digest
    cohorts: list[Cohort]
    owned_run_count: Nat
    owned_run_digest: Digest
    errors: list[ID]


class PersistenceMapping(Record):
    role: Literal["os", "datastore", "objects", "redis"]
    root_relative_path: ID
    filesystem_id: ID
    external: StrictBool


class Image(Record):
    image_id: ID
    kind: Literal["root"]
    persistence: list[PersistenceMapping]
    sha256: Digest
    size_bytes: Pos
    stopped_ns: Nat
    sealed_ns: Nat
    format: Literal["raw"]
    # Safe identity only, no local disk path/export permissions.
    device: Pos
    inode: Pos


class Seal(Role):
    role: Literal["seal"] = "seal"
    seal_id: ID
    fixture_id: ID
    fixture_manifest_digest: Digest
    source_digest: Digest
    configuration_digest: Digest
    policy_id: ID
    migration: ID
    projector_source_version: ID
    read_algorithm_version: ID
    generation: ID
    metric_version: ID
    completed_corpus_batch_id: ID
    images: list[Image]
    cohorts: list[Cohort]


class Limits(Record):
    workers: Pos
    per_worker: Pos
    claim_batch_size: Pos
    pool_size: Pos
    max_overflow: Nat
    subject_concurrency: Pos
    judge_concurrency: Pos
    environment_concurrency: Pos
    physical_global: Pos | None
    physical_user: Pos | None
    physical_provider: Pos | None
    policy_id: ID


class Environment(Role):
    role: Literal["environment"] = "environment"
    server_host_id: ID
    client_host_id: ID
    server_cpu_ids: list[Nat]
    client_cpu_ids: list[Nat]
    host_memory_bytes: Pos
    server_memory_bytes: Pos
    client_memory_bytes: Pos
    overhead_memory_bytes: Pos
    postgres_memory_max_bytes: Pos
    cgroup_id: ID
    disk_id: ID
    disk_rotational: StrictBool
    disk_direct_io: StrictBool
    architecture: Literal["x86_64"]
    os: ID
    browser: ID
    postgres: ID
    python: ID
    playwright: ID
    qemu_binary_sha256: Digest
    viewport: list[Pos]
    runtime_limits: Limits


class BlockNode(Record):
    node_name: ID
    driver: Literal["file", "raw", "qcow2"]
    child: ID | None
    backing: ID | None
    cache_direct: StrictBool
    cache_no_flush: StrictBool
    read_only: StrictBool
    device: Pos | None
    inode: Pos | None
    fd_direct: StrictBool | None
    image_id: ID | None


class Reset(Record):
    reset_id: ID
    sample_id: ID
    window_id: ID
    seal_id: ID
    process_id: Pos
    process_start_ticks: Pos
    process_uuid: ID
    socket_peer_pid: Pos
    socket_device: Pos
    socket_inode: Pos
    executable_sha256: Digest
    argv_digest: Digest
    boot_id: ID
    coordinator_clock_id: ID
    launched_ns: Nat
    minimal_ready_ns: Nat
    load_ready_ns: Nat
    target_dispatch_ns: Nat
    stopped_ns: Nat
    kvm_enabled: StrictBool
    ram_resumed: StrictBool
    shared_mounts: list[ID]
    qmp_version: ID
    qmp_commands: list[ID]
    qmp_uuid: ID
    guest_cpu_count: Pos
    guest_memory_bytes: Pos
    nodes: list[BlockNode]
    target_reads_before_dispatch: list[ID]
    incidental_warming: list[ID]
    # Concrete QGA fixed helper and response identity, not arbitrary shell.
    helper_module: Literal["scripts.execution_capacity.guest_main"]
    helper_action: Literal["cold-window"]
    helper_nonce: ID
    helper_response_nonce: ID
    helper_source_digest: Digest


class Resets(Role):
    role: Literal["resets"] = "resets"
    resets: list[Reset]


class Link(Record):
    namespace_inode: Pos
    ifindex: Pos
    peer_ifindex: Pos
    address: ID
    route_destination: ID
    route_ifindex: Pos
    qdisc_kind: Literal["netem"]
    delay_us: Literal[25000]
    rate_bps: Literal[20000000]
    default_route: StrictBool


class Calibration(Record):
    window_id: ID
    clock_id: ID
    phase: Literal["baseline", "pre", "window", "post"]
    ordinal: Nat
    action: Literal["echo", "upload", "download"]
    start_ns: Nat
    end_ns: Nat
    transport: Literal["tcp"]
    bytes: Pos
    elapsed_ns: Pos
    echo_rtt_ns: Pos | None
    bits_per_second: Number
    coverage: Literal["discrete-probe"]

    @model_validator(mode="after")
    def fixed_probe_bytes(self):
        if self.bytes != (32 if self.action == "echo" else 8 * 1024**2):
            raise ValueError("fixed calibration probe bytes differ")
        return self


class CalibrationRule(Record):
    added_rtt_min_ns: Pos
    added_rtt_max_ns: Pos
    throughput_min_bps: Pos
    throughput_max_bps: Pos


class CalibrationPhaseInterval(Record):
    window_id: ID
    clock_id: ID
    phase: Literal["baseline", "pre", "window", "post"]
    scheduled_ns: Nat
    deadline_ns: Nat
    before_ns: Nat
    after_ns: Nat


class Network(Role):
    role: Literal["network"] = "network"
    backend: Literal["owned-netns-veth-qemu-usernet"]
    host_link: Link
    client_link: Link
    forward_bound_address: ID
    guest_ports: list[Pos]
    global_ip_forward_changed: StrictBool
    nat_changed: StrictBool
    cdp_delay_ms: Literal[0]
    context_ids: list[ID]
    validity: CalibrationRule
    calibrations: list[Calibration]
    phase_intervals: list[CalibrationPhaseInterval]


class Diagnostics(Role):
    role: Literal["diagnostics"] = "diagnostics"
    queries: list[Query]


class Disposition(Record):
    resource_id: ID
    kind: Literal[
        "qemu_process",
        "qmp_socket",
        "overlay",
        "broker",
        "upload",
        "claim",
        "lease",
        "provider_call",
    ]
    state: Literal["absent", "retained", "settled", "pending", "quarantined"]
    observed_ns: Nat
    owner_id: ID
    identity_digest: Digest
    physical_slots: Nat
    proof_kind: Literal[
        "proc-starttime-absent",
        "socket-inode-absent",
        "protected-inode-retained",
        "broker-lookup-absent",
        "object-readback",
        "claim-readback",
        "lease-readback",
        "settlement-readback",
    ]


class Cleanup(Role):
    rounds: list[RoundInventory]
    role: Literal["cleanup"] = "cleanup"
    cohorts: list[Cohort]
    total: Counts
    dispositions: list[Disposition]
    pending: list[ID]
    quarantined: list[ID]
    status: Literal["retained_immutable", "disposed_owned_runtime"]


ROLES = {
    c.model_fields["role"].default: c
    for c in (
        Protocol,
        Measurements,
        Workload,
        Seal,
        Environment,
        Resets,
        Network,
        Diagnostics,
        Cleanup,
        C2c,
    )
}


class Artifact(Record):
    shard_index: Nat = 0
    shard_count: Pos = 1
    path: ID
    sha256: Digest
    size_bytes: Pos
    role: Literal[
        "protocol",
        "measurements",
        "workload",
        "seal",
        "environment",
        "resets",
        "network",
        "diagnostics",
        "cleanup",
        "fixture",
        "c2c",
    ]
    schema_version: StrictInt


class Binding(Record):
    revision: ID
    dirty_tree_digest: Digest
    images: dict[ID, StrictStr | dict[ID, StrictStr]]
    migration: ID
    fixture_manifest_digest: Digest


class WorkloadSummary(Record):
    windows: Nat
    browsers_per_window: Nat
    active_runs_per_window: Nat
    effective_progress_records: Nat
    rate_rule: Literal["each-1s-bin-at-least-2-v1"]


class CacheSummary(Record):
    independent_cold_resets: Nat
    warm_prewarms: Nat
    backend: Literal["linux-x86_64-kvm-qemu"]


class CohortSummary(Record):
    origin: SourceOrigin
    cohort_id: ID
    kind: Literal[
        "standard", "step_capacity", "evaluation_subject", "evaluation_judge", "live", "admission"
    ]
    scope_id: ID
    run_ids_digest: Digest
    source: Counts
    view: Counts
    parity_digest: Digest


class OrderedCommitment(Record):
    count: Nat
    ordered_sha256: Digest


class FrameSummary(OrderedCommitment):
    p50_ms: Number | None
    p95_ms: Number | None
    max_ms: Number | None

    @model_validator(mode="after")
    def population(self):
        values = (self.p50_ms, self.p95_ms, self.max_ms)
        if (self.count == 0 and any(value is not None for value in values)) or (
            self.count > 0
            and (
                any(value is None for value in values)
                or not 0 <= self.p50_ms <= self.p95_ms <= self.max_ms
            )
        ):
            raise ValueError("frame statistic population mismatch")
        return self


class LongTaskSummary(OrderedCommitment):
    over_200ms_count: Nat
    max_ms: Number | None

    @model_validator(mode="after")
    def population(self):
        if self.over_200ms_count > self.count or (self.count == 0) != (self.max_ms is None):
            raise ValueError("long task statistic population mismatch")
        if self.max_ms is not None and (
            self.max_ms < 0 or (self.max_ms > 200) != (self.over_200ms_count > 0)
        ):
            raise ValueError("long task threshold mismatch")
        return self


class FramesSummary(FrameSummary):
    long_tasks: LongTaskSummary


class CleanupSummary(Record):
    status: Literal["retained_immutable", "disposed_owned_runtime"]
    pending: list[ID]
    quarantined: list[ID]
    retained_scopes: OrderedCommitment
    retained_formal_events: Nat
    retained_observations: Nat
    cohorts: OrderedCommitment
    total: Counts


class Summary(Record):
    environment: Environment
    workload: WorkloadSummary
    cache: CacheSummary
    cleanup: CleanupSummary
    warm: dict[ID, list[Number]]
    cold: dict[ID, list[Number]]
    latency: dict[ID, list[Number]]
    heap: dict[Literal["peak_mib", "visible_dom_rows"], Number]
    frames: FramesSummary
    step_capacity: "StepSummary"
    errors: list[ID]


class StepSummary(Record):
    cohort_id: ID
    scope_id: ID
    run_id: ID
    seal_id: ID
    parity_digest: Digest
    visible_steps: Nat
    formal_events: Nat
    observations: Nat
    warm: dict[ID, list[Number]]
    cold: dict[ID, list[Number]]


class Report(Summary):
    schema_version: Literal[4]
    binding: Binding
    completed_binding: Binding
    started_at: ID
    finished_at: ID
    fixture_counts: dict[ID, Nat]
    attempt_id: ID
    protocol_id: ID
    protocol_digest: Digest
    artifacts: list[Artifact]


class FixtureTarget(Record):
    environment: Literal["test"]
    runtime_id: ID
    database_id: ID
    scope_id: ID


class Fixture(Record):
    schema_version: Literal[1]
    fixture_id: ID
    seed: StrictInt
    scope_ids: list[ID]
    counts: dict[ID, Nat]
    window_start: ID
    window_end: ID
    status: Literal["planned"]
    target: FixtureTarget
    ownership_journal: Literal["ownership.jsonl"]


class NativeControl(Record):
    wire_version: Literal[1]
    command: Literal["open", "progress", "close", "cancel"]
    attempt_id: ID
    protocol_id: ID
    sample_id: ID
    action_id: ID
    context_id: ID
    page_id: ID
    window_id: ID
    clock_id: ID
    sequence: Pos
    source_receipt_id: ID
    progress: Progress | None
    opened_ns: NativeNS | None = None

    @model_validator(mode="after")
    def payload(self):
        if (self.command == "progress") != (self.progress is not None):
            raise ValueError("closed control payload mismatch")
        if (self.command == "open") != (self.opened_ns is not None):
            raise ValueError("actual open control payload mismatch")
        if self.progress and self.progress.window_id != self.window_id:
            raise ValueError("foreign source window")
        return self
