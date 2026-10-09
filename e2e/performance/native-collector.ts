/** Owned Playwright/CDP collector. Importing this module creates no browser,
 * sockets, process, timers or files. C3b owns launcher/pidfd/attempt lifecycle. */
import type {
  Browser,
  BrowserContext,
  Page,
  CDPSession,
  Request,
  Response,
} from "@playwright/test";
import { readFile, readlink } from "node:fs/promises";
import { createReadStream } from "node:fs";
import { StringDecoder } from "node:string_decoder";
import { createHash } from "node:crypto";
import { appApi } from "../support/api";
import {
  cropProgressPNG,
  digest,
  encodeFailureSnapshot,
  envelope,
  FirstResponse,
  LIMITS,
  qualifyBuild,
  REVIEWED_BUILDS,
  requireFact,
  validatePNG,
  validateWire,
  type FailureSourceRecordRef,
  type FailureSourceRef,
  type NativeCommand,
  type Obj,
} from "./native-contract";

export type HostClock = {
  now(): bigint;
  wait(ms: number, signal: AbortSignal): Promise<void>;
};
// ACK only after the host stamps its own receipt and durably appends the record.
export type Emit = (line: Buffer) => Promise<number>;
export type Deployment = Readonly<{
  origin: string;
  principalId: string;
  workspaceId: string;
  credentials: Readonly<{ email_or_username: string; password: string }>;
}>;
export type OwnedContext = {
  context: BrowserContext;
  page: Page;
  cdp: CDPSession;
  browserCDP: CDPSession;
  contextId: string;
  pageId: string;
  targetId: string;
  principalId: string;
  prepared: boolean;
  document: Obj | null;
  requests: Map<Request, Obj>;
  stream: Obj | null;
  failed: string | null;
  trace: Obj[];
  traceBytes: number;
  traceDone: Promise<void>;
  finishTrace: () => void;
  traceLost: boolean;
  observerInstalled: bigint;
  rendererCandidates: Map<number, string>;
  finalizedMapping?: {
    frameId: string;
    documentId: string;
    pid: number;
    start: string;
  };
};

/** Installed before target navigation. Local observations are never host ns. */
function installObservers() {
  const w = window as unknown as Obj;
  let serial = 0,
    mutations = 0,
    lost = false,
    previous: number | undefined;
  const nodes = new WeakMap<Element, Obj>();
  const frames: Obj[] = [],
    tasks: Obj[] = [];
  const track = (element: Element) => {
    if (nodes.has(element)) return;
    if (++serial > 65536) {
      lost = true;
      return;
    }
    nodes.set(element, {
      serial,
      text: element.textContent ?? "",
      identifier: element.getAttribute("elementtiming"),
      attrs: element.outerHTML.split(">")[0],
      entry: null,
    });
  };
  const scan = (root: ParentNode) =>
    root.querySelectorAll("[elementtiming]").forEach(track);
  const observeMutations = (records: MutationRecord[]) => {
    for (const row of records) {
      // Portals, siblings and ancestors can occlude the native content. Retain
      // every document mutation, including a transient change reversed later.
      mutations++;
      if (row.type === "childList")
        for (const node of row.addedNodes)
          if (node instanceof Element) {
            if (node.hasAttribute("elementtiming")) track(node);
            scan(node);
          }
    }
  };
  const mutation = new MutationObserver(observeMutations);
  mutation.observe(document, {
    subtree: true,
    childList: true,
    characterData: true,
    attributes: true,
  });
  if (
    !PerformanceObserver.supportedEntryTypes.includes("element") ||
    !PerformanceObserver.supportedEntryTypes.includes("longtask")
  )
    lost = true;
  else {
    new PerformanceObserver((list) => {
      for (const e of list.getEntries() as unknown as Obj[]) {
        const element = e.element as Element | null;
        if (!element || e.name !== "text-paint") continue;
        const owner = nodes.get(element);
        if (
          !owner ||
          owner.entry ||
          owner.identifier !== e.identifier ||
          owner.text !== (element.textContent ?? "") ||
          owner.attrs !== element.outerHTML.split(">")[0]
        ) {
          lost = true;
          continue;
        }
        owner.entry = {
          renderTime: e.renderTime,
          rect: [
            e.intersectionRect.x,
            e.intersectionRect.y,
            e.intersectionRect.width,
            e.intersectionRect.height,
          ],
        };
      }
    }).observe({ type: "element", buffered: true });
    new PerformanceObserver((list) => {
      for (const e of list.getEntries()) {
        if (tasks.length === 2048) lost = true;
        else tasks.push([e.startTime, e.startTime + e.duration]);
      }
    }).observe({ type: "longtask", buffered: true });
  }
  const frame = (now: number) => {
    if (previous !== undefined) {
      if (frames.length === 8192) lost = true;
      else frames.push({ at: now, interval: now - previous });
    }
    previous = now;
    requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
  document.addEventListener("visibilitychange", () => {
    mutations++;
  });
  for (const event of [
    "resize",
    "scroll",
    "animationstart",
    "animationend",
    "transitionrun",
    "transitionend",
    "fullscreenchange",
  ])
    window.addEventListener(
      event,
      () => {
        mutations++;
      },
      true,
    );
  w.__capacityNative = {
    sample: () => {
      const result = {
        local_ms: performance.now(),
        frame_intervals_ms: frames.map((f) => f.interval),
        frame_times_ms: frames.map((f) => f.at),
        long_tasks: tasks.splice(0),
        mounted_rows: document.querySelectorAll("[data-trace-row]").length,
        visible: document.visibilityState === "visible",
        observer_lost: lost,
      };
      frames.length = 0;
      return result;
    },
    read: (operation: string, step: string | null) => {
      observeMutations(mutation.takeRecords());
      scan(document);
      requireVisible();
      if (
        typeof document.getAnimations !== "function" ||
        document
          .getAnimations()
          .some((a) => a.playState === "running" || a.pending)
      )
        throw new Error("unstable-content");
      const view =
        operation === "analysis"
          ? "analysis"
          : operation === "matrix"
            ? "matrix"
            : operation === "switch"
              ? "detail"
              : null;
      const roots = [
        ...document.querySelectorAll<HTMLElement>("[data-native-view]"),
      ].filter((e) =>
        view
          ? e.dataset.nativeView === view
          : ["task", "trace"].includes(e.dataset.nativeView ?? ""),
      );
      const ready = roots.filter((e) => e.dataset.nativeReady === "true");
      if (ready.length !== 1) throw new Error("not-ready");
      const root = ready[0],
        d = root.dataset;
      const kinds =
        operation === "analysis"
          ? ["analysis-values"]
          : operation === "matrix"
            ? ["matrix-result"]
            : operation === "switch"
              ? ["detail-identity", "detail-status"]
              : operation === "live_visible"
                ? ["progress"]
                : ["summary", "step", "trace-step"];
      const owners: Obj[] = [];
      for (const element of root.querySelectorAll<HTMLElement>(
        "[data-native-content]",
      )) {
        if (
          !kinds.includes(element.dataset.nativeContent ?? "") ||
          (operation === "live_visible" && element.dataset.publicStep !== step)
        )
          continue;
        const rect = element.getBoundingClientRect();
        if (
          rect.bottom <= 0 ||
          rect.right <= 0 ||
          rect.top >= innerHeight ||
          rect.left >= innerWidth
        )
          continue;
        let current: Element | null = element;
        while (current) {
          const style = getComputedStyle(current);
          if (
            style.display === "none" ||
            style.visibility !== "visible" ||
            Number(style.opacity) !== 1 ||
            style.filter !== "none" ||
            style.transform !== "none" ||
            style.clipPath !== "none"
          )
            throw new Error("hidden-content");
          current = current.parentElement;
        }
        if (
          rect.width <= 0 ||
          rect.height <= 0 ||
          rect.left < 0 ||
          rect.top < 0 ||
          rect.right > innerWidth ||
          rect.bottom > innerHeight
        )
          throw new Error("hidden-content");
        for (const [x, y] of [
          [rect.left + 1, rect.top + 1],
          [rect.right - 1, rect.bottom - 1],
          [rect.left + rect.width / 2, rect.top + rect.height / 2],
        ]) {
          const hit = document.elementFromPoint(x, y);
          if (!hit || !(element.contains(hit) || hit.contains(element)))
            throw new Error("hidden-content");
        }
        const range = document.createRange();
        range.selectNodeContents(element);
        const textRects = [...range.getClientRects()]
          .filter((r) => r.width > 0 && r.height > 0)
          .map((r) => [r.x, r.y, r.width, r.height]);
        if (
          !textRects.length ||
          textRects.some(
            (r) =>
              r[0] < 0 ||
              r[1] < 0 ||
              r[0] + r[2] > innerWidth ||
              r[1] + r[3] > innerHeight,
          )
        )
          throw new Error("hidden-content");
        if (operation === "live_visible" && element.childElementCount !== 0)
          throw new Error("hidden-content");
        const owner = nodes.get(element);
        if (!owner?.entry || !element.isConnected)
          throw new Error("missing-element-timing");
        if (
          owner.text !== element.textContent ||
          owner.attrs !== element.outerHTML.split(">")[0]
        )
          throw new Error("unstable-content");
        owners.push({
          serial: owner.serial,
          content: element.dataset.nativeContent,
          text: owner.text,
          identifier: owner.identifier,
          run_id: element.dataset.publicRun ?? null,
          step_id: element.dataset.publicStep ?? null,
          revision: element.dataset.publicRevision ?? null,
          result_id: element.dataset.publicResult ?? null,
          rect: [rect.x, rect.y, rect.width, rect.height],
          native_rect: owner.entry.rect,
          text_rects: textRects,
          render_time_ms: owner.entry.renderTime,
        });
      }
      if (!owners.length || owners.length > 256 || lost)
        throw new Error(lost ? "observer-loss" : "missing-element-timing");
      if (
        operation === "switch" &&
        !["detail-identity", "detail-status"].every((kind) =>
          owners.some((o) => o.content === kind),
        )
      )
        throw new Error("not-ready");
      return {
        dataset: {
          scope_id: d.publicScope,
          run_id: d.publicRun ?? null,
          public_id: d.publicAt ?? d.publicWatermark ?? d.publicSnapshot,
          revision: d.publicRevision ?? d.publicMetricVersion,
          step_id:
            operation === "switch"
              ? (d.publicStep ?? null)
              : operation === "live_visible"
                ? (owners[0]?.step_id ?? null)
                : null,
        },
        owners,
        mutation_sequence: mutations,
        visible: true,
      };
    },
  };
  function requireVisible() {
    if (
      document.visibilityState !== "visible" ||
      innerWidth !== 1440 ||
      innerHeight !== 900
    )
      throw new Error("hidden-content");
  }
}

function route(intent: Obj, base = false): string {
  const enc = encodeURIComponent;
  if (intent.operation === "analysis") {
    const { grain, timezone, ...filters } = intent.filters;
    return `/analysis?${new URLSearchParams({ grain, timezone, filters: JSON.stringify(Object.fromEntries(Object.entries(filters).filter(([, v]) => v !== null))) })}`;
  }
  if (intent.operation === "matrix")
    return `/evaluations/batches/${enc(intent.batch_id)}`;
  const query = new URLSearchParams({ run: intent.run_id, view: intent.view });
  if (intent.step_id && !base) query.set("step", intent.step_id);
  if (base && intent.history) query.set("at", intent.history.anchor_at);
  return `/sessions/${enc(intent.session_id)}?${query}`;
}
export class NativeCollector {
  private used = new Set<string>();
  private busy = new Set<string>();
  private sequences = new Map<string, number>();
  private prepared = new Set<string>();
  private controls = new Map<
    string,
    {
      sequence: number;
      closed: boolean;
      cancelled: boolean;
      progress: Obj[];
      ready: boolean;
      readyNs: bigint | null;
      open: Obj | null;
    }
  >();
  private traceSession: CDPSession | null = null;
  private traceComplete: Promise<Obj> | null = null;
  private owners: OwnedContext[] = [];
  private outputs = new Map<string, Promise<void>>();
  private jobs = new Map<
    string,
    {
      command: NativeCommand;
      emit: Emit;
      owned: OwnedContext;
      failed: boolean;
      measuredComplete: boolean;
    }
  >();
  private captureLocks = new Set<string>();
  private traceDraining = false;
  private traceDrained = false;
  private traceFailed = false;
  private key(c: Obj) {
    return JSON.stringify([
      c.attempt_id,
      c.protocol_id,
      c.sample_id,
      c.action_id,
      c.context_id,
      c.page_id,
      c.window_id,
      c.clock_id,
    ]);
  }
  acceptControl(command: NativeCommand, control: Obj) {
    requireFact(this.terminalFailure === null, "failure-terminal-closed");
    validateWire("control", control);
    const key = this.key(command),
      state = this.controls.get(key);
    requireFact(
      state &&
        !state.closed &&
        Object.entries(envelope(command)).every(([k, v]) => control[k] === v) &&
        control.sequence === state.sequence + 1,
      "foreign-or-duplicate-control",
    );
    state.sequence = control.sequence;
    if (control.command === "open") {
      const received = this.clock.now();
      requireFact(
        command.intent.operation === "live_visible" &&
          command.window_open_ns === null &&
          state.ready &&
          state.readyNs !== null &&
          BigInt(control.opened_ns) >= state.readyNs &&
          state.open === null &&
          BigInt(control.opened_ns) <= received &&
          received < BigInt(command.deadline_ns),
        "foreign-or-duplicate-open",
      );
      state.open = {
        kind: "open",
        control_sequence: control.sequence,
        source_receipt_id: control.source_receipt_id,
        opened_ns: control.opened_ns,
        received_ns: received.toString(),
      };
    } else if (control.command === "close") state.closed = true;
    else if (control.command === "cancel") {
      state.cancelled = true;
      this.failOwnership("cancelled");
    } else {
      requireFact(
        control.progress?.window_id === command.window_id &&
          control.progress.run_id === command.intent.run_id &&
          state.progress.length < 120,
        "progress-mapping",
      );
      state.progress.push(control.progress);
    }
  }
  constructor(
    private browser: Browser,
    private deployment: Deployment,
    private clock: HostClock,
    private signal: AbortSignal,
    private inspectProcess: typeof processIdentity = processIdentity,
  ) {}
  // Measurement cancellation never asserts cancellation of the underlying CDP
  // promise. Pending operations and acquired private bytes have this owner until
  // actual settlement or explicit durable failure-evidence acknowledgement.
  private operationOrdinal = 0;
  private outstanding = new Map<number, Obj>();
  private measurementAbort = new AbortController();
  private failureStarted: bigint | null = null;
  private failureCause: string | null = null;
  private failureArtifacts = new Map<
    string,
    {
      owner: string;
      bytes: Buffer;
      observedBytes: number;
      offset: number;
      sha256: string;
      receivedNs: string;
      sourceRefs: Map<number, FailureSourceRecordRef>;
    }
  >();
  private failureInventory = new Map<
    string,
    {
      owner: string;
      observedBytes: number;
      retainedBytes: number;
      sha256: string;
      receivedNs: string;
      acknowledgedBytes: number;
      sourceRefs: Map<number, FailureSourceRecordRef>;
    }
  >();
  private heldBytes = 0;
  private discardedBytes = 0;
  private failureRetainedBytes = 0;
  private failureRetainedArtifacts = 0;
  private failureSeenIds = new Set<string>();
  private sourceRefCount = 0;
  private retentionBusy = false;
  private terminalFailure: { owner: string; bytes: Buffer } | null = null;
  private lateHandles = new Map<
    string,
    { owner: string; kind: string; handle: unknown }
  >();
  transferLateHandle(
    command: NativeCommand,
    operationId: string,
    durableReceiptId: string,
  ) {
    requireFact(this.terminalFailure === null, "failure-terminal-closed");
    const entry = this.lateHandles.get(operationId);
    requireFact(
      entry?.owner === this.key(command) && durableReceiptId.length > 0,
      "foreign-late-handle",
    );
    this.lateHandles.delete(operationId);
    return entry.handle;
  }
  private failOwnership(code: string) {
    if (this.failureStarted === null) {
      this.failureStarted = this.clock.now();
      this.failureCause = code;
      this.failureRetainedBytes = [...this.failureArtifacts.values()].reduce(
        (sum, artifact) => sum + artifact.bytes.length + artifact.offset,
        0,
      );
      this.failureRetainedArtifacts = this.failureArtifacts.size;
      this.failureSeenIds = new Set(this.failureArtifacts.keys());
      for (const [id, artifact] of this.failureArtifacts)
        this.failureInventory.set(id, {
          owner: artifact.owner,
          observedBytes: artifact.observedBytes,
          retainedBytes: artifact.bytes.length + artifact.offset,
          sha256: artifact.sha256,
          receivedNs: artifact.receivedNs,
          acknowledgedBytes: artifact.offset,
          sourceRefs: new Map(artifact.sourceRefs),
        });
      this.measurementAbort.abort();
    }
  }
  private hold(
    command: NativeCommand,
    id: string,
    bytes: Buffer,
    observedBytes = bytes.length,
  ) {
    requireFact(this.terminalFailure === null, "failure-terminal-closed");
    requireFact(
      !this.failureArtifacts.has(id) && !this.failureSeenIds.has(id),
      "duplicate-private-artifact",
    );
    const expired =
      this.failureStarted !== null &&
      this.clock.now() >= this.failureStarted + 10000000000n;
    const available = expired
      ? 0
      : Math.max(
          0,
          Math.min(
            128 * 1024 * 1024 - this.heldBytes,
            this.failureStarted === null
              ? 128 * 1024 * 1024
              : 128 * 1024 * 1024 - this.failureRetainedBytes,
          ),
        );
    const count =
      this.failureArtifacts.size < 128 &&
      (this.failureStarted === null || this.failureRetainedArtifacts < 128)
        ? Math.min(bytes.length, available)
        : 0;
    this.discardedBytes += observedBytes - count;
    if (count < observedBytes) this.failOwnership("retention-quota");
    if (count) {
      const retained = Buffer.from(bytes.subarray(0, count));
      this.failureArtifacts.set(id, {
        owner: this.key(command),
        bytes: retained,
        observedBytes,
        offset: 0,
        sha256: digest(retained),
        receivedNs: this.clock.now().toString(),
        sourceRefs: new Map(),
      });
      this.heldBytes += count;
      if (this.failureStarted !== null) {
        this.failureSeenIds.add(id);
        this.failureRetainedBytes += count;
        this.failureRetainedArtifacts++;
        const artifact = this.failureArtifacts.get(id)!;
        this.failureInventory.set(id, {
          owner: artifact.owner,
          observedBytes: artifact.observedBytes,
          retainedBytes: count,
          sha256: artifact.sha256,
          receivedNs: artifact.receivedNs,
          acknowledgedBytes: 0,
          sourceRefs: new Map(),
        });
      }
    }
  }
  private release(id: string) {
    const artifact = this.failureArtifacts.get(id);
    if (artifact) {
      this.sourceRefCount -= artifact.sourceRefs.size;
      this.heldBytes -= artifact.bytes.length;
      this.failureArtifacts.delete(id);
    }
  }
  private recordAckSource(
    id: string,
    ref: FailureSourceRecordRef,
    raw: Buffer,
  ) {
    // A record that arrives after failure ownership began cannot retroactively
    // change an already emitted callback's provenance.
    if (this.failureStarted !== null || this.sourceRefCount >= 4096) return;
    const artifact = this.failureArtifacts.get(id);
    if (
      !artifact ||
      artifact.sourceRefs.has(ref.artifact_offset) ||
      ref.bytes !== raw.length ||
      ref.artifact_offset + ref.bytes > artifact.bytes.length ||
      !artifact.bytes
        .subarray(ref.artifact_offset, ref.artifact_offset + ref.bytes)
        .equals(raw)
    )
      return;
    artifact.sourceRefs.set(ref.artifact_offset, Object.freeze({ ...ref }));
    this.sourceRefCount++;
  }
  failureState(command: NativeCommand) {
    requireFact(this.failureStarted !== null, "failure-state-not-ready");
    const now = this.clock.now();
    const snapshot = {
      ...envelope(command),
      wire_version: 2,
      observed_ns: now.toString(),
      failure_ns: this.failureStarted?.toString() ?? null,
      retention_deadline_ns:
        this.failureStarted === null
          ? null
          : (this.failureStarted + 10000000000n).toString(),
      cause: this.failureCause,
      discarded_bytes: this.discardedBytes,
      held_bytes: this.heldBytes,
      operations: [...this.outstanding.values()].map((o) => ({ ...o })),
      handles: [...this.lateHandles]
        .filter(([, h]) => h.owner === this.key(command))
        .map(([operation_id, h]) => ({ operation_id, kind: h.kind })),
      artifacts: [...this.failureInventory]
        .filter(([, a]) => a.owner === this.key(command))
        .map(([id, a]) => ({
          artifact_id: id,
          observed_bytes: a.observedBytes,
          retained_bytes: a.retainedBytes,
          acknowledged_bytes: a.acknowledgedBytes,
          sha256: a.sha256,
          received_ns: a.receivedNs,
          source_record_refs: [...a.sourceRefs.values()]
            .filter(
              (ref) => ref.artifact_offset + ref.bytes <= a.acknowledgedBytes,
            )
            .map((ref) => ({ ...ref })),
        })),
      disposition: [...this.outstanding.values()].some(
        (o) => o.state === "pending",
      )
        ? "pending"
        : this.discardedBytes ||
            this.failureArtifacts.size ||
            this.lateHandles.size
          ? "partial"
          : "settled",
    };
    validateWire("failure", snapshot);
    return snapshot;
  }
  /** Canonical diagnostic bytes only. They can change while late work or a
   * timed-out failure emit still owns a callback. Use the terminal barrier for
   * any host close commitment. */
  failureSnapshotBytes(command: NativeCommand): Buffer {
    validateWire("command", command);
    requireFact(this.used.has(this.key(command)), "foreign-failure-snapshot");
    return encodeFailureSnapshot(this.failureState(command));
  }
  /** Commit one command's final failed state only after every callback outcome is
   * known. A timed-out drain may still own an in-flight durable ACK; its ordinary
   * snapshot is diagnostic until this synchronous barrier succeeds. */
  terminalFailureSnapshotBytes(command: NativeCommand): Buffer {
    validateWire("command", command);
    const owner = this.key(command);
    requireFact(
      this.used.size === 1 && this.used.has(owner),
      "foreign-failure-snapshot",
    );
    if (this.terminalFailure !== null) {
      requireFact(
        this.terminalFailure.owner === owner,
        "foreign-failure-snapshot",
      );
      return Buffer.from(this.terminalFailure.bytes);
    }
    requireFact(
      this.failureStarted !== null &&
        !this.retentionBusy &&
        ![...this.outstanding.values()].some((o) => o.state === "pending"),
      "failure-terminal-not-ready",
    );
    const bytes = encodeFailureSnapshot(this.failureState(command));
    this.terminalFailure = { owner, bytes };
    return Buffer.from(bytes);
  }
  readFailureChunk(command: NativeCommand, id: string) {
    const a = this.failureArtifacts.get(id);
    requireFact(
      this.failureStarted !== null && a?.owner === this.key(command),
      "foreign-private-artifact",
    );
    const bytes = Buffer.from(a.bytes.subarray(0, LIMITS.chunk));
    const recordRef = this.failureInventory.get(id)?.sourceRefs.get(a.offset);
    const sourceRef: FailureSourceRef =
      recordRef?.bytes === bytes.length
        ? { ...recordRef }
        : { kind: "independent" };
    return {
      ...envelope(command),
      wire_version: 2,
      artifact_id: id,
      observed_bytes: a.observedBytes,
      retained_bytes: a.bytes.length + a.offset,
      retained_sha256: a.sha256,
      artifact_received_ns: a.receivedNs,
      failure_ns: this.failureStarted.toString(),
      retention_deadline_ns: (this.failureStarted + 10000000000n).toString(),
      offset: a.offset,
      bytes: bytes.length,
      sha256: digest(bytes),
      source_record_ref: sourceRef,
      data: bytes,
    };
  }
  acknowledgeFailureChunk(command: NativeCommand, receipt: Obj) {
    requireFact(this.terminalFailure === null, "failure-terminal-closed");
    const row = this.readFailureChunk(command, receipt.artifact_id);
    requireFact(
      Object.entries({ ...envelope(command), wire_version: 2 }).every(
        ([key, value]) => Object.hasOwn(receipt, key) && receipt[key] === value,
      ) &&
        receipt.offset === row.offset &&
        receipt.bytes === row.bytes &&
        receipt.sha256 === row.sha256 &&
        receipt.observed_bytes === row.observed_bytes &&
        receipt.retained_bytes === row.retained_bytes &&
        receipt.retained_sha256 === row.retained_sha256 &&
        receipt.artifact_received_ns === row.artifact_received_ns &&
        receipt.failure_ns === row.failure_ns &&
        receipt.retention_deadline_ns === row.retention_deadline_ns &&
        receipt.source_record_ref !== null &&
        typeof receipt.source_record_ref === "object" &&
        Object.keys(receipt.source_record_ref).length ===
          Object.keys(row.source_record_ref).length &&
        Object.entries(row.source_record_ref).every(
          ([key, value]) =>
            Object.hasOwn(receipt.source_record_ref, key) &&
            receipt.source_record_ref[key] === value,
        ) &&
        typeof receipt.durable_receipt_id === "string" &&
        receipt.durable_receipt_id.length > 0,
      "failure-retention-ack",
    );
    const a = this.failureArtifacts.get(receipt.artifact_id)!;
    a.offset += row.bytes;
    this.failureInventory.get(receipt.artifact_id)!.acknowledgedBytes =
      a.offset;
    this.heldBytes -= row.bytes;
    // Copy only the remaining bytes so acknowledged storage is not retained by
    // a subarray alias to the complete original allocation.
    a.bytes = Buffer.from(a.bytes.subarray(row.bytes));
    if (a.bytes.length === 0) this.release(receipt.artifact_id);
  }
  /** Separate fixed ten-second failure-evidence owner; never extends measurement.
   * A pending late ACK remains registered. State stays partial until bytes have a
   * matching durable receipt, and pending until actual operations settle. */
  async drainFailureEvidence(
    command: NativeCommand,
    emit: (
      chunk: ReturnType<NativeCollector["readFailureChunk"]>,
    ) => Promise<Obj>,
    signal: AbortSignal,
  ) {
    requireFact(this.terminalFailure === null, "failure-terminal-closed");
    requireFact(
      this.failureStarted !== null && !this.retentionBusy,
      "failure-retention-not-ready",
    );
    this.retentionBusy = true;
    const deadline = this.failureStarted + 10000000000n;
    try {
      while (this.clock.now() < deadline && !signal.aborted) {
        const entry = [...this.failureArtifacts].find(
          ([, a]) => a.owner === this.key(command),
        );
        if (entry) {
          const chunk = this.readFailureChunk(command, entry[0]);
          await this.runOwned(
            command,
            () => emit(chunk),
            "failure-retention-emit",
            (value) => this.acknowledgeFailureChunk(command, value),
            deadline,
            signal,
            true,
          );
        } else if (
          [...this.outstanding.values()].some((o) => o.state === "pending")
        ) {
          await this.runOwned(
            command,
            () => this.clock.wait(50, signal),
            "failure-settlement-wait",
            undefined,
            deadline,
            signal,
            true,
          );
        } else break;
      }
    } catch {
      /* State explicitly retains pending/partial evidence; never success. */
    } finally {
      this.retentionBusy = false;
    }
    return this.failureState(command);
  }
  private async bounded<T>(
    command: NativeCommand,
    operation: () => Promise<T>,
    stage = "native",
    onResult?: (value: T) => void,
  ): Promise<T> {
    return this.runOwned(
      command,
      operation,
      stage,
      onResult,
      BigInt(command.deadline_ns),
      this.signal,
      false,
    );
  }
  private async runOwned<T>(
    command: NativeCommand,
    operation: () => Promise<T>,
    stage: string,
    onResult: ((value: T) => void) | undefined,
    deadline: bigint,
    signal: AbortSignal,
    retention: boolean,
  ): Promise<T> {
    requireFact(this.terminalFailure === null, "failure-terminal-closed");
    if (signal.aborted) {
      this.failOwnership("cancelled");
      throw new Error("cancelled");
    }
    if (this.clock.now() >= deadline) {
      this.failOwnership("deadline");
      throw new Error("deadline");
    }
    requireFact(
      retention || this.failureStarted === null,
      this.failureCause ?? "native-failed",
    );
    if (this.outstanding.size >= 128) {
      const settled = [...this.outstanding].find(
        ([, o]) => o.state !== "pending",
      );
      if (settled) this.outstanding.delete(settled[0]);
    }
    if (this.outstanding.size >= 128) {
      this.failOwnership("native-operation-quota");
      throw new Error("native-operation-quota");
    }
    const id = ++this.operationOrdinal,
      entry: Obj = {
        operation_id: `operation-${id}`,
        sample_id: command.sample_id,
        action_id: command.action_id,
        stage,
        started_ns: this.clock.now().toString(),
        settled_ns: null,
        state: "pending",
        late: false,
      };
    this.outstanding.set(id, entry);
    const timeout = new AbortController();
    let cancel = () => {};
    const interrupted = new Promise<never>((_, reject) => {
      cancel = () => {
        this.failOwnership("cancelled");
        reject(new Error(this.failureCause ?? "cancelled"));
      };
      signal.addEventListener("abort", cancel, { once: true });
      if (!retention)
        this.measurementAbort.signal.addEventListener("abort", cancel, {
          once: true,
        });
    });
    // Register before dispatch. The settlement continuation stays attached after
    // timeout; late screenshots are retained by onResult before any rejection.
    const actual = Promise.resolve()
      .then(operation)
      .then(
        (value) => {
          entry.settled_ns = this.clock.now().toString();
          entry.late =
            this.failureStarted !== null ||
            BigInt(entry.settled_ns) >= deadline ||
            signal.aborted;
          try {
            if (onResult) onResult(value);
            else if (
              entry.late &&
              !retention &&
              [
                "new-context",
                "new-page",
                "new-session",
                "browser-session",
              ].includes(stage)
            ) {
              this.lateHandles.set(entry.operation_id, {
                owner: this.key(command),
                kind: stage,
                handle: value,
              });
            } else if (entry.late && !retention) {
              let raw: Buffer;
              try {
                raw = Buffer.from(JSON.stringify(value) ?? "null");
              } catch {
                raw = Buffer.from("unserializable-result");
              }
              // Late opaque CDP/body results are private bounded diagnostic prefixes.
              this.hold(
                command,
                `late-${id}`,
                raw.subarray(0, 65536),
                raw.length,
              );
            }
          } catch (error) {
            entry.state = "rejected";
            entry.error_digest = digest(String(error));
            if (
              !entry.late ||
              (retention &&
                BigInt(entry.settled_ns) < deadline &&
                !signal.aborted)
            )
              this.outstanding.delete(id);
            throw error;
          }
          entry.state = "fulfilled";
          if (
            !entry.late ||
            (retention &&
              BigInt(entry.settled_ns) < deadline &&
              !signal.aborted)
          )
            this.outstanding.delete(id);
          if (
            !retention &&
            (this.failureStarted !== null ||
              this.clock.now() >= deadline ||
              signal.aborted)
          ) {
            this.failOwnership(signal.aborted ? "cancelled" : "deadline");
            throw new Error(this.failureCause!);
          }
          return value;
        },
        (error) => {
          entry.settled_ns = this.clock.now().toString();
          entry.state = "rejected";
          entry.late = this.failureStarted !== null;
          entry.error_digest = digest(String(error));
          if (
            !entry.late ||
            (retention &&
              BigInt(entry.settled_ns) < deadline &&
              !signal.aborted)
          )
            this.outstanding.delete(id);
          throw error;
        },
      );
    try {
      return await Promise.race([
        actual,
        interrupted,
        this.clock
          .wait(
            Number((deadline - this.clock.now() + 999999n) / 1000000n),
            timeout.signal,
          )
          .then(() => {
            this.failOwnership("deadline");
            throw new Error("deadline");
          }),
      ]);
    } finally {
      timeout.abort();
      signal.removeEventListener("abort", cancel);
      this.measurementAbort.signal.removeEventListener("abort", cancel);
    }
  }
  private async send(
    command: NativeCommand,
    emit: Emit,
    observation: Obj,
    onDurableAck?: (sequence: number) => void,
  ) {
    requireFact(this.terminalFailure === null, "failure-terminal-closed");
    const key = this.key(command),
      previous = this.outputs.get(key) ?? Promise.resolve();
    const next = this.bounded(command, async () => {
      await this.bounded(command, () => previous, "output-predecessor");
      const sequence = (this.sequences.get(key) ?? 0) + 1;
      this.sequences.set(key, sequence);
      requireFact(sequence <= LIMITS.records, "transport");
      const row = {
        ...envelope(command),
        sequence,
        received_ns: this.clock.now().toString(),
        observation,
      };
      validateWire("record", row);
      const line = Buffer.from(JSON.stringify(row) + "\n");
      requireFact(line.length <= LIMITS.line, "transport");
      const ack = await this.bounded(command, () => emit(line));
      requireFact(ack === sequence, "transport");
      onDurableAck?.(sequence);
    });
    this.outputs.set(key, next);
    await next;
  }
  /** Neutral authentication only. Secrets remain in memory, never emit/errors. */
  async openNativeContext(command: NativeCommand): Promise<OwnedContext> {
    validateWire("command", command);
    requireFact(
      (command.intent.operation === "live_visible") ===
        (command.window_open_ns === null),
      "invalid-command",
    );
    requireFact(
      command.intent.scope_id === this.deployment.workspaceId,
      "foreign-scope",
    );
    const context = await this.bounded(
      command,
      () =>
        this.browser.newContext({
          baseURL: this.deployment.origin,
          viewport: { width: 1440, height: 900 },
          deviceScaleFactor: 1,
        }),
      "new-context",
    );
    try {
      await this.bounded(command, () =>
        context.exposeBinding("__capacityNativeWake", () => {}),
      );
      await this.bounded(command, () =>
        context.addInitScript(installObservers),
      );
      const page = await this.bounded(
        command,
        () => context.newPage(),
        "new-page",
      );
      const cdp = await this.bounded(
        command,
        () => context.newCDPSession(page),
        "new-session",
      );
      const browserCDP = await this.bounded(
        command,
        () => this.browser.newBrowserCDPSession(),
        "browser-session",
      );
      let finishTrace = () => {};
      const traceDone = new Promise<void>((resolve) => {
        finishTrace = resolve;
      });
      const owned: OwnedContext = {
        context,
        page,
        cdp,
        browserCDP,
        contextId: command.context_id,
        pageId: command.page_id,
        targetId: "",
        principalId: "",
        prepared: false,
        document: null,
        requests: new Map(),
        stream: null,
        failed: null,
        trace: [],
        traceBytes: 0,
        traceDone,
        finishTrace,
        traceLost: false,
        observerInstalled: this.clock.now(),
        rendererCandidates: new Map(),
      };
      cdp.on("Runtime.executionContextCreated", ({ context: c }) => {
        if (c.auxData?.isDefault)
          owned.document = {
            frameId: c.auxData.frameId,
            uniqueId: c.uniqueId,
            id: c.id,
          };
      });
      cdp.on("Runtime.executionContextsCleared", () => {
        owned.document = null;
      });
      cdp.on("Network.loadingFailed", (e) => {
        if (owned.stream?.requestId === e.requestId) owned.failed = "not-ready";
      });
      cdp.on("Network.loadingFinished", (e) => {
        if (owned.stream?.requestId === e.requestId) owned.failed = "not-ready";
      });
      cdp.on("Network.requestWillBeSent", (e) => {
        const url = new URL(e.request.url);
        if (
          url.pathname ===
            `/api/execution-runs/${command.intent.run_id}/events/stream` &&
          url.origin === this.deployment.origin
        ) {
          const scope = Object.entries(e.request.headers).find(
            ([k]) => k.toLowerCase() === "x-workspace-id",
          )?.[1];
          if (scope !== command.intent.scope_id) {
            owned.failed = "not-ready";
            return;
          }
          owned.stream = {
            startedNs: this.clock.now().toString(),
            responseNs: null,
            eventId: null,
            cursor: null,
            requestId: e.requestId,
            frameId: e.frameId,
            status: 0,
            eventSeen: false,
            partial: "",
            lastEventId:
              Object.entries(e.request.headers).find(
                ([k]) => k.toLowerCase() === "last-event-id",
              )?.[1] ?? null,
          };
        }
      });
      const streamData = (encoded: string) => {
        if (!owned.stream) return;
        const s = owned.stream;
        s.partial += Buffer.from(encoded, "base64").toString("utf8");
        if (Buffer.byteLength(s.partial) > 96 * 1024) {
          owned.failed = "observer-loss";
          return;
        }
        let end: number;
        while ((end = s.partial.search(/\r?\n\r?\n/)) >= 0) {
          const part = s.partial.slice(0, end);
          s.partial = s.partial.slice(end).replace(/^\r?\n\r?\n/, "");
          const lines = part.split(/\r?\n/);
          if (lines.includes("event: refresh")) {
            owned.failed = "not-ready";
            continue;
          }
          if (lines.includes("event: execution")) {
            try {
              const event = JSON.parse(
                lines
                  .filter((l: string) => l.startsWith("data: "))
                  .map((l: string) => l.slice(6))
                  .join("\n"),
              );
              requireFact(
                event.run_id === command.intent.run_id &&
                  typeof event.cursor === "string" &&
                  event.cursor !== s.lastEventId,
                "not-ready",
              );
              requireFact(typeof event.event_id === "string", "not-ready");
              s.eventSeen = true;
              s.eventId = event.event_id;
              s.cursor = event.cursor;
            } catch {
              owned.failed = "not-ready";
            }
          }
        }
      };
      cdp.on("Network.responseReceived", (e) => {
        if (owned.stream?.requestId === e.requestId) {
          owned.stream.responseNs = this.clock.now().toString();
          owned.stream.status = e.response.status;
          owned.stream.mime = e.response.mimeType;
          if (
            e.response.status !== 200 ||
            e.response.mimeType !== "text/event-stream"
          )
            owned.failed = "not-ready";
          else
            void this.bounded(command, () =>
              cdp.send("Network.streamResourceContent", {
                requestId: e.requestId,
              }),
            )
              .then((r) => {
                if (r.bufferedData) streamData(r.bufferedData);
              })
              .catch(() => {
                owned.failed = "observer-loss";
              });
        }
      });
      cdp.on("Network.dataReceived", (e) => {
        if (owned.stream?.requestId === e.requestId && e.data)
          streamData(e.data);
      });

      await this.bounded(command, () => cdp.send("Runtime.enable"));
      await this.bounded(command, () => cdp.send("Page.enable"));
      await this.bounded(command, () => cdp.send("Network.enable"));
      owned.targetId = (
        await this.bounded(command, () => cdp.send("Target.getTargetInfo"))
      ).targetInfo.targetId;
      if (!this.traceSession) {
        this.traceSession = browserCDP;
        this.traceComplete = new Promise((resolve) =>
          browserCDP.once("Tracing.tracingComplete", (event) =>
            resolve({ ...event, observed_ns: this.clock.now().toString() }),
          ),
        );
        await this.bounded(command, () =>
          browserCDP.send("Tracing.start", {
            categories: "loading,disabled-by-default-devtools.timeline",
            transferMode: "ReturnAsStream",
            streamFormat: "json",
          }),
        );
      }
      this.owners.push(owned);
      await this.bounded(command, () => page.goto("/login"));
      const login = await this.bounded(command, () =>
        appApi<Obj>(page, "/auth/login", {
          method: "POST",
          body: this.deployment.credentials,
        }),
      );
      const me = await this.bounded(command, () =>
        appApi<Obj>(page, "/auth/me"),
      );
      requireFact(
        login.data.id === this.deployment.principalId &&
          me.data.id === login.data.id &&
          me.data.status === "active",
        "not-ready",
      );
      owned.principalId = me.data.id;
      await this.bounded(command, () =>
        page.evaluate(
          (scope) =>
            localStorage.setItem("opencitadel-active-workspace", scope),
          this.deployment.workspaceId,
        ),
      );
      await this.bounded(command, () => page.goto("/"));
      await this.bounded(command, () =>
        page
          .getByRole("button", { name: /Workspace|工作区/ })
          .waitFor({ state: "visible" }),
      );
      return owned;
    } catch (error) {
      // C3b owns cleanup. An unresolved context/CDP operation stays in the
      // operation ledger; no unbounded close or assumed cancellation here.
      throw error;
    }
  }
  /** Explicit base-view preparation; no firstscreen/analysis/matrix prefetch. */
  async prepareNative(command: NativeCommand, owned: OwnedContext, emit: Emit) {
    requireFact(this.terminalFailure === null, "failure-terminal-closed");
    requireFact(
      ["switch", "history"].includes(command.intent.operation) &&
        (command.intent.operation !== "history" || command.mode === "warm") &&
        !this.prepared.has(command.sample_id),
      "invalid-preparation",
    );
    this.prepared.add(command.sample_id);
    const started = this.clock.now();
    await this.bounded(command, () =>
      owned.page.goto(route(command.intent, true)),
    );
    await this.bounded(command, () =>
      owned.page
        .locator('[data-native-view][data-native-ready="true"]')
        .first()
        .waitFor(),
    );
    const read = await this.bounded(command, () =>
      owned.page
        .locator('[data-native-view][data-native-ready="true"]')
        .first()
        .evaluate((e) => ({
          run_id: (e as HTMLElement).dataset.publicRun,
          at: (e as HTMLElement).dataset.publicAt,
          revision: (e as HTMLElement).dataset.publicRevision,
        })),
    );
    requireFact(
      read.run_id === command.intent.run_id && read.at && read.revision,
      "not-ready",
    );
    owned.prepared = true;
    await this.send(command, emit, {
      kind: "preparation",
      started_ns: started.toString(),
      completed_ns: this.clock.now().toString(),
      run_id: read.run_id,
      at: read.at,
      revision: read.revision,
    });
  }
  async collectNative(command: NativeCommand, owned: OwnedContext, emit: Emit) {
    requireFact(this.terminalFailure === null, "failure-terminal-closed");
    validateWire("command", command);
    const key = this.key(command);
    requireFact(
      !this.used.has(key) &&
        !this.busy.has(owned.pageId) &&
        owned.contextId === command.context_id &&
        owned.pageId === command.page_id,
      "duplicate-or-foreign-collection",
    );
    this.used.add(key);
    this.busy.add(owned.pageId);
    this.controls.set(key, {
      sequence: 0,
      closed: false,
      cancelled: false,
      progress: [],
      ready: false,
      readyNs: null,
      open: null,
    });
    this.jobs.set(key, {
      command,
      emit,
      owned,
      failed: false,
      measuredComplete: false,
    });
    const state = this.controls.get(key)!;
    const i = command.intent;
    let responseFailure = false,
      first: Obj | null = null,
      latest: Obj | null = null,
      requestOrdinal = 0,
      captures = 0,
      ready = false,
      painted = false,
      lastPublic = "";
    const captureFacts: Obj[] = [];
    let responseWork = Promise.resolve();
    let mapped = 0;
    const publishMappings = async () => {
      if (i.operation !== "live_visible") return;
      for (const capture of captureFacts) {
        const owner = capture.read.owners.find(
          (o: Obj) => o.step_id === i.step_id,
        );
        const index = state.progress.findIndex(
          (p) => p.public_message === owner?.text,
        );
        if (index < mapped) continue;
        const rows = state.progress.slice(mapped, index + 1);
        if (!rows.length) continue;
        await this.send(command, emit, {
          kind: "progress-mapping",
          capture_id: capture.capture_id,
          progress_ids: rows.map((p) => p.progress_id),
          painted_progress_id: rows.at(-1)!.progress_id,
          step_id: i.step_id,
          revision: Number(owner.revision),
          public_message: owner.text,
        });
        mapped = index + 1;
      }
    };
    let trigger = 0n;
    let firstGate: FirstResponse;
    let historyAt: string | null = null;
    const onRequest = (request: Request) => {
      if (trigger > 0n && request.frame() === owned.page.mainFrame()) {
        if (requestOrdinal >= 1024) {
          responseFailure = true;
          return;
        }
        owned.requests.set(request, {
          id: `request-${++requestOrdinal}`,
          at: this.clock.now(),
        });
      }
    };
    const onResponse = (response: Response) => {
      const received = this.clock.now();
      responseWork = responseWork
        .then(async () => {
          const request = response.request(),
            stamp = owned.requests.get(request);
          if (!stamp) return;
          const url = new URL(response.url());
          if (url.origin !== this.deployment.origin) return;
          const expected =
            i.operation === "analysis"
              ? "/api/execution-analysis/summary"
              : i.operation === "matrix"
                ? `/api/evaluation/batches/${i.batch_id}/summary`
                : i.operation === "switch"
                  ? `/api/execution-runs/${i.run_id}/steps/${encodeURIComponent(i.step_id)}`
                  : `/api/execution-runs/${i.run_id}/view`;
          if (
            i.operation === "history" &&
            url.pathname === `/api/execution-runs/${i.run_id}/timeline` &&
            (url.searchParams.has("direction") ||
              url.searchParams.has("target_time"))
          ) {
            try {
              requireFact(
                historyAt === null && response.status() === 200,
                "response-binding",
              );
              if (i.history.direction === "time")
                requireFact(
                  url.searchParams.get("target_time") === i.history.target_time,
                  "response-binding",
                );
              else
                requireFact(
                  url.searchParams.get("anchor_at") === i.history.anchor_at &&
                    url.searchParams.get("direction") ===
                      (i.history.direction === "previous" ? "before" : "after"),
                  "response-binding",
                );
              const body = await this.bounded(command, () => response.json());
              requireFact(
                body.code === 200 &&
                  body.data.run_id === i.run_id &&
                  typeof body.data.at === "string",
                "response-binding",
              );
              historyAt = body.data.at;
            } catch {
              responseFailure = true;
            }
            return;
          }
          if (
            url.pathname !== expected ||
            (i.operation === "history" && historyAt === null)
          )
            return;
          try {
            const headers = await this.bounded(command, () =>
              request.allHeaders(),
            );
            requireFact(
              headers["x-workspace-id"] === i.scope_id &&
                response.status() === 200,
              "response-binding",
            );
            if (i.operation === "analysis") {
              requireFact(
                !url.searchParams.has("watermark"),
                "response-binding",
              );
              const { grain, timezone, ...filters } = i.filters;
              requireFact(
                url.searchParams.get("grain") === grain &&
                  url.searchParams.get("timezone") === timezone &&
                  JSON.stringify(
                    JSON.parse(url.searchParams.get("filters") ?? "{}"),
                  ) ===
                    JSON.stringify(
                      Object.fromEntries(
                        Object.entries(filters).filter(([, v]) => v !== null),
                      ),
                    ),
                "response-binding",
              );
            }
            const envelope = await this.bounded(command, () => response.json());
            requireFact(
              envelope.code === 200 && envelope.data,
              "response-binding",
            );
            const data = envelope.data;
            if (i.operation === "history")
              requireFact(
                data.at === historyAt &&
                  url.searchParams.get("at") === historyAt,
                "response-binding",
              );
            const public_id =
              i.operation === "analysis"
                ? data.watermark
                : i.operation === "matrix"
                  ? data.snapshot_id
                  : data.at;
            const revision =
              i.operation === "analysis"
                ? data.metric_version
                : i.operation === "matrix"
                  ? String(data.evaluation_revision)
                  : String(data.projection_revision ?? data.revision);
            requireFact(
              typeof public_id === "string" && revision !== "undefined",
              "response-binding",
            );
            if (i.run_id)
              requireFact(
                (data.run?.run_id ?? data.run_id) === i.run_id,
                "response-binding",
              );
            const row = {
              request_id: stamp.id,
              response_id: `response-${stamp.id}`,
              action_id: command.action_id,
              request_ns: stamp.at,
              received_ns: received,
              scope_id: i.scope_id,
              run_id: i.run_id,
              step_id:
                i.operation === "switch"
                  ? data.step_id
                  : i.operation === "live_visible"
                    ? data.steps?.find(
                        (step: Obj) => step.step_id === i.step_id,
                      )?.step_id
                    : null,
              public_id,
              revision,
            };
            const binding = first
              ? new FirstResponse(command, trigger).accept(row)
              : firstGate.accept(row);
            if (first && i.operation !== "live_visible")
              throw new Error("response-binding");
            latest = binding;
            if (!first) {
              first = binding;
              await this.send(command, emit, { kind: "binding", binding });
            } else
              await this.send(command, emit, {
                kind: "public-response",
                binding,
              });
          } catch {
            responseFailure = true;
            await this.send(command, emit, {
              kind: "error",
              code: "response-binding",
              stage: "first-response",
              request_id: stamp.id,
              evidence_digest: digest(`${response.status()}:${url.pathname}`),
            });
          }
        })
        .catch(() => {
          responseFailure = true;
        });
    };
    owned.page.on("request", onRequest);
    owned.page.on("response", onResponse);
    try {
      requireFact(
        i.operation === "live_visible"
          ? command.window_open_ns === null
          : command.window_open_ns !== null &&
              this.clock.now() >= BigInt(command.window_open_ns),
        "not-ready",
      );
      if (i.operation !== "live_visible")
        await this.send(command, emit, {
          kind: "resource-boundary",
          phase: "begin",
          observed_ns: this.clock.now().toString(),
        });
      trigger = this.clock.now();
      await this.send(command, emit, {
        kind: "action",
        operation: i.operation,
        trigger_ns: trigger.toString(),
      });
      firstGate = new FirstResponse(command, trigger);
      if (i.operation === "switch") {
        requireFact(owned.prepared, "not-ready");
        await this.bounded(command, () =>
          owned.page
            .locator(`[data-step=${JSON.stringify(i.step_id)}]`)
            .first()
            .click(),
        );
      } else if (i.operation === "history") {
        if (command.mode === "cold")
          await this.bounded(command, () => owned.page.goto(route(i, true)));
        else requireFact(owned.prepared, "not-ready");
        const controls = owned.page.getByTestId("playback-controls");
        if (i.history.direction === "time")
          await this.bounded(command, () =>
            controls
              .locator('input[type="range"]')
              .fill(String(Date.parse(i.history.target_time))),
          );
        else
          await this.bounded(command, () =>
            controls
              .locator("button")
              .nth(i.history.direction === "previous" ? 0 : 1)
              .click(),
          );
      } else await this.bounded(command, () => owned.page.goto(route(i)));
      let opened: bigint | null =
        command.window_open_ns === null ? null : BigInt(command.window_open_ns);
      let resourceEnd: bigint | null =
        opened === null
          ? null
          : opened +
            BigInt(command.resource_offset_ns) +
            BigInt(command.resource_duration_ns);
      let scrolled = false,
        resourceClosed = false;
      while (!state.closed) {
        requireFact(!this.signal.aborted && !state.cancelled, "cancelled");
        requireFact(!responseFailure && !owned.failed, "response-binding");
        requireFact(this.clock.now() < BigInt(command.deadline_ns), "deadline");
        await this.bounded(command, () => responseWork, "response-queue");
        if (opened === null && state.open) {
          opened = BigInt(state.open.opened_ns);
          await this.send(command, emit, state.open);
          const begin = this.clock.now();
          requireFact(
            opened <= begin &&
              begin < opened + BigInt(command.resource_offset_ns),
            "late-resource-open",
          );
          await this.send(command, emit, {
            kind: "resource-boundary",
            phase: "begin",
            observed_ns: begin.toString(),
          });
          resourceEnd =
            opened +
            BigInt(command.resource_offset_ns) +
            BigInt(command.resource_duration_ns);
        }
        const document = owned.document;
        requireFact(document?.uniqueId && document.frameId, "not-ready");
        const before = this.clock.now();
        const heap = await this.bounded(command, () =>
          owned.cdp.send("Runtime.getHeapUsage"),
        );
        const sample = await this.bounded(command, () =>
          owned.page.evaluate(() =>
            (window as unknown as Obj).__capacityNative.sample(),
          ),
        );
        const after = this.clock.now();
        requireFact(
          owned.document?.uniqueId === document.uniqueId,
          "unstable-content",
        );
        const parts = Math.max(
          1,
          Math.ceil(sample.frame_times_ms.length / 128),
          Math.ceil(sample.long_tasks.length / 128),
        );
        for (let part = 0; part < parts; part++)
          await this.send(command, emit, {
            kind: "resource",
            phase:
              opened === null ? "preopen" : resourceClosed ? "tail" : "capture",
            document_id: document.uniqueId,
            frame_id: document.frameId,
            execution_context_id: document.uniqueId,
            ...sample,
            frame_intervals_ms: sample.frame_intervals_ms.slice(
              part * 128,
              (part + 1) * 128,
            ),
            frame_times_ms: sample.frame_times_ms.slice(
              part * 128,
              (part + 1) * 128,
            ),
            long_tasks: sample.long_tasks.slice(part * 128, (part + 1) * 128),
            host_before_ns: before.toString(),
            host_after_ns: after.toString(),
            heap_bytes: part === 0 ? heap.usedSize : null,
            mounted_rows: part === 0 ? sample.mounted_rows : null,
          });
        requireFact(sample.visible && !sample.observer_lost, "observer-loss");
        if (
          latest &&
          !this.traceDraining &&
          (!painted || i.operation === "live_visible")
        ) {
          let read: Obj | null = null;
          try {
            read = await this.read(command, owned, i);
          } catch (e) {
            if (
              !["not-ready", "missing-element-timing"].includes(
                (e as Error).message,
              )
            )
              throw e;
          }
          if (read) {
            requireFact(
              Object.entries(read.target).every(
                ([k, v]) => (latest as Obj).target[k] === v,
              ),
              "response-binding",
            );
            if (i.operation === "live_visible")
              requireFact(
                owned.stream?.eventSeen &&
                  owned.stream.status === 200 &&
                  owned.stream.frameId === read.frame_id,
                "not-ready",
              );
            if (!ready) {
              await this.send(command, emit, {
                kind: "ready",
                principal_id: owned.principalId,
                scope_id: i.scope_id,
                run_id: i.run_id,
                target_id: owned.targetId,
                frame_id: read.frame_id,
                subscription_request_id: owned.stream?.requestId ?? null,
                subscription_event_seen: !!owned.stream?.eventSeen,
                subscription_event_id: owned.stream?.eventId ?? null,
                subscription_cursor: owned.stream?.cursor ?? null,
                subscription_started_ns: owned.stream?.startedNs ?? null,
                subscription_response_ns: owned.stream?.responseNs ?? null,
                observer_installed_ns: owned.observerInstalled.toString(),
              });
              ready = true;
              state.ready = true;
              state.readyNs = this.clock.now();
            }
            const publicKey = JSON.stringify([
              read.target,
              read.owners.map((o: Obj) => [o.serial, o.text]),
            ]);
            if (publicKey !== lastPublic) {
              requireFact(captures < command.max_captures, "capture-failed");
              captures++;
              await this.send(command, emit, { kind: "readback", ...read });
              const fact = await this.captureNative(
                command,
                owned,
                read,
                `capture-${captures}`,
                emit,
              );
              captureFacts.push({ capture_id: fact.capture_id, read });
              lastPublic = publicKey;
              painted = true;
            }
          }
        }
        if (
          painted &&
          !scrolled &&
          opened !== null &&
          this.clock.now() >= opened + BigInt(command.resource_offset_ns)
        ) {
          await this.scroll(command, owned, emit);
          scrolled = true;
        }
        if (
          scrolled &&
          !resourceClosed &&
          resourceEnd !== null &&
          this.clock.now() >= resourceEnd
        ) {
          await this.send(command, emit, {
            kind: "resource-boundary",
            phase: "end",
            observed_ns: this.clock.now().toString(),
          });
          resourceClosed = true;
        }
        await publishMappings();
        this.jobs.get(key)!.measuredComplete =
          resourceClosed &&
          painted &&
          ready &&
          mapped === state.progress.length;
        if (state.closed) break;
        await this.bounded(command, () => this.clock.wait(50, this.signal));
      }
      await this.bounded(command, () => responseWork, "response-queue");
      requireFact(
        painted &&
          ready &&
          scrolled &&
          resourceEnd !== null &&
          this.clock.now() >= resourceEnd &&
          !responseFailure,
        "not-ready",
      );
      if (i.operation === "live_visible") {
        await publishMappings();
        if (mapped < state.progress.length)
          await this.send(command, emit, {
            kind: "error",
            code: "progress-mapping",
            stage: "unpainted-source-updates",
            request_id: null,
            evidence_digest: digest(
              JSON.stringify(
                state.progress.slice(mapped).map((p) => p.progress_id),
              ),
            ),
          });
      }
      // Source closure only ends observations. The measured trace is drained
      // separately before client-done; finishNative emits lifecycle status.
    } catch (error) {
      this.jobs.get(key)!.failed = true;
      const code = String((error as Error).message);
      const allowed = [
        "response-binding",
        "not-ready",
        "hidden-content",
        "unstable-content",
        "missing-element-timing",
        "observer-loss",
        "capture-failed",
        "deadline",
        "cancelled",
        "transport",
        "progress-mapping",
        "unsupported-native-build",
        "renderer-binding",
        "late-resource-open",
      ];
      await this.send(command, emit, {
        kind: "error",
        code: allowed.includes(code) ? code : "capture-failed",
        stage: "collect",
        request_id: null,
        evidence_digest: null,
      });
      throw error;
    } finally {
      owned.page.off("request", onRequest);
      owned.page.off("response", onResponse);
      this.busy.delete(owned.pageId);
    }
  }
  private async read(
    command: NativeCommand,
    owned: OwnedContext,
    intent: Obj,
  ): Promise<Obj> {
    const doc = owned.document;
    requireFact(doc?.frameId && doc.uniqueId, "not-ready");
    const observed = await this.bounded(
      command,
      () =>
        owned.cdp.send("Runtime.evaluate", {
          expression: `window.__capacityNative.read(${JSON.stringify(intent.operation)},${JSON.stringify(intent.step_id)})`,
          uniqueContextId: doc.uniqueId,
          returnByValue: true,
        }),
      "content-readback",
    );
    if (observed.exceptionDetails) {
      const description = observed.result.description ?? "";
      const code = [
        "not-ready",
        "hidden-content",
        "unstable-content",
        "missing-element-timing",
        "observer-loss",
      ].find((k) => description.includes(k));
      throw new Error(code ?? "not-ready");
    }
    requireFact(owned.document?.uniqueId === doc.uniqueId, "unstable-content");
    const raw = observed.result.value;
    requireFact(
      raw && raw.dataset.scope_id === intent.scope_id,
      "response-binding",
    );
    return {
      target: raw.dataset,
      owners: raw.owners,
      mutation_sequence: raw.mutation_sequence,
      visible: raw.visible,
      document_id: doc.uniqueId,
      frame_id: doc.frameId,
      execution_context_id: doc.uniqueId,
      target_id: owned.targetId,
    };
  }
  async captureNative(
    command: NativeCommand,
    owned: OwnedContext,
    read: Obj,
    captureId: string,
    emit: Emit,
  ) {
    requireFact(this.terminalFailure === null, "failure-terminal-closed");
    requireFact(
      !this.traceFailed && !this.captureLocks.has(owned.targetId),
      "concurrent-capture",
    );
    this.captureLocks.add(owned.targetId);
    try {
      return await this.performCapture(command, owned, read, captureId, emit);
    } finally {
      this.captureLocks.delete(owned.targetId);
    }
  }
  private async performCapture(
    command: NativeCommand,
    owned: OwnedContext,
    read: Obj,
    captureId: string,
    emit: Emit,
  ) {
    const readbackSequence = this.sequences.get(this.key(command))!;
    const processes = await this.bounded(command, () =>
      owned.browserCDP.send("SystemInfo.getProcessInfo"),
    );
    const candidates = [];
    for (const p of processes.processInfo.filter(
      (p) => p.type === "renderer",
    )) {
      const identity = await this.bounded(command, () =>
        this.inspectProcess(p.id),
      );
      const old = owned.rendererCandidates.get(p.id);
      requireFact(!old || old === identity.start, "renderer-binding");
      owned.rendererCandidates.set(p.id, identity.start);
      candidates.push({ pid: p.id, start: identity.start });
    }
    requireFact(
      candidates.length > 0 && candidates.length <= 128,
      "renderer-binding",
    );
    if (this.traceDrained) {
      const mapping = owned.finalizedMapping;
      requireFact(
        mapping &&
          owned.document?.uniqueId === mapping.documentId &&
          owned.document.frameId === mapping.frameId &&
          candidates.some(
            (p) => p.pid === mapping.pid && p.start === mapping.start,
          ),
        "renderer-binding",
      );
    }
    const dispatched = this.clock.now();
    const privateId = `png-${digest(this.key(command)).slice(0, 16)}-${captureId}`;
    const result = await this.bounded(
      command,
      () =>
        owned.cdp.send("Page.captureScreenshot", {
          format: "png",
          fromSurface: true,
          captureBeyondViewport: false,
        }),
      "native-screenshot",
      (value) => {
        const encoded = value.data;
        const observed =
          Math.floor((encoded.length * 3) / 4) -
          (encoded.endsWith("==") ? 2 : encoded.endsWith("=") ? 1 : 0);
        const raw = Buffer.from(
          encoded.slice(0, Math.ceil(LIMITS.image / 3) * 4),
          "base64",
        );
        this.hold(command, privateId, raw, observed);
      },
    );
    const received = this.clock.now(),
      bytes = Buffer.from(result.data, "base64");
    let retained: Buffer = bytes,
      crop: number[] | null = null,
      channels = 0,
      postcheck = this.clock.now(),
      qualification = null;
    let failure: unknown = null;
    try {
      requireFact(bytes.toString("base64") === result.data, "capture-failed");
      channels = validatePNG(bytes).channels;
      const post = await this.read(command, owned, command.intent);
      postcheck = this.clock.now();
      await this.send(command, emit, {
        ...post,
        kind: "postcheck",
        capture_id: captureId,
        readback_sequence: readbackSequence,
      });
      requireFact(
        JSON.stringify(post) === JSON.stringify(read),
        "unstable-content",
      );
      const facts = await this.buildFacts(command, owned);
      qualification = qualifyBuild(facts, REVIEWED_BUILDS);
      if (
        command.intent.operation === "live_visible" &&
        captureId !== "capture-1" &&
        qualification
      ) {
        requireFact(
          read.owners.length === 1 && read.owners[0].content === "progress",
          "capture-failed",
        );
        const owner = read.owners[0],
          region = cropProgressPNG(bytes, [
            owner.native_rect,
            ...owner.text_rects,
          ]);
        // The padded complete text hull must belong to this coherent content.
        const [x, y, w, h] = region.rect,
          [ox, oy, ow, oh] = owner.rect;
        requireFact(
          x >= ox - 4 &&
            y >= oy - 4 &&
            x + w <= ox + ow + 4 &&
            y + h <= oy + oh + 4,
          "capture-failed",
        );
        retained = region.bytes;
        crop = region.rect;
      }
    } catch (error) {
      failure = error;
      retained = bytes;
      crop = null;
    }
    // A failed capture always retains its complete original, never just a hash.
    for (
      let offset = 0, index = 0;
      offset < retained.length;
      offset += LIMITS.chunk, index++
    ) {
      const raw = retained.subarray(offset, offset + LIMITS.chunk);
      await this.send(
        command,
        emit,
        failure
          ? {
              kind: "private-chunk",
              artifact_id: captureId,
              purpose: "rejected-capture",
              chunk_index: index,
              data: raw.toString("base64"),
            }
          : {
              kind: "image",
              capture_id: captureId,
              chunk_index: index,
              data: raw.toString("base64"),
            },
        failure
          ? (sequence) =>
              this.recordAckSource(
                privateId,
                {
                  kind: "native-record",
                  sequence,
                  purpose: "rejected-capture",
                  artifact_id: captureId,
                  chunk_index: index,
                  artifact_offset: offset,
                  bytes: raw.length,
                },
                raw,
              )
          : undefined,
      );
    }
    if (failure) {
      this.release(privateId);
      throw failure;
    }
    const record = {
      kind: "capture",
      capture_id: captureId,
      request_id: `screenshot-${captureId}`,
      target_id: owned.targetId,
      readback_sequence: readbackSequence,
      renderer_candidates: candidates,
      dispatched_ns: dispatched.toString(),
      received_ns: received.toString(),
      postcheck_ns: postcheck.toString(),
      sha256: digest(bytes),
      bytes: bytes.length,
      chunks: Math.ceil(retained.length / LIMITS.chunk),
      width: 1440,
      height: 900,
      qualification: qualification
        ? "matched-reviewed-build"
        : "pending-runtime-qualification",
      retention: crop ? "progress-region" : "full",
      retained_sha256: digest(retained),
      retained_bytes: retained.length,
      crop_rect: crop,
      transform: crop ? "png-lossless-text-hull-pad4-v1" : "png-native-full-v1",
      channels,
    };
    await this.send(command, emit, record);
    this.release(privateId);
    requireFact(qualification, "unsupported-native-build");
    return record;
  }
  private async scroll(
    command: NativeCommand,
    owned: OwnedContext,
    emit: Emit,
  ) {
    const selector =
      command.intent.operation === "matrix"
        ? '[data-native-scroll="matrix"]'
        : command.intent.view === "debug"
          ? '[data-testid="trace-view"] [role="tree"]'
          : "main";
    const locator = owned.page.locator(selector).first();
    const geometry = () =>
      locator.evaluate((e) => [e.scrollTop, e.scrollHeight, e.clientHeight]);
    const before = await this.bounded(command, () => geometry());
    requireFact(before[2] > 0, "hidden-content");
    if (before[1] <= before[2]) {
      requireFact(!command.require_positive_scroll, "capture-failed");
      await this.send(command, emit, {
        kind: "scroll",
        before,
        after: before,
        reason: "observed-nonoverflow",
      });
      return;
    }
    const box = await this.bounded(command, () => locator.boundingBox());
    requireFact(box && box.width > 0 && box.height > 0, "hidden-content");
    await this.bounded(command, () =>
      owned.page.mouse.move(box.x + box.width / 2, box.y + box.height / 2),
    );
    await this.bounded(command, () =>
      owned.page.mouse.wheel(0, Math.min(400, before[2] / 2)),
    );
    await this.bounded(command, () => this.clock.wait(50, this.signal));
    const after = await this.bounded(command, () => geometry());
    requireFact(Math.abs(after[0] - before[0]) > 0, "capture-failed");
    await this.send(command, emit, {
      kind: "scroll",
      before,
      after,
      reason: "realized-native-wheel",
    });
  }
  private async buildFacts(
    command: NativeCommand,
    owned: OwnedContext,
  ): Promise<Obj> {
    const version = await this.bounded(command, () =>
      owned.browserCDP.send("Browser.getVersion"),
    );
    const args = await this.bounded(command, () =>
      owned.browserCDP.send("Browser.getBrowserCommandLine"),
    );
    const processes = await this.bounded(command, () =>
      owned.browserCDP.send("SystemInfo.getProcessInfo"),
    );
    const browsers = processes.processInfo.filter((p) => p.type === "browser");
    requireFact(browsers.length === 1, "renderer-binding");
    const process = await this.bounded(command, () =>
      this.inspectProcess(browsers[0].id),
    );
    return {
      product: version.product,
      revision: version.revision,
      protocol_version: version.protocolVersion,
      argv: args.arguments,
      platform: process.platform,
      executable_sha256: process.sha256,
      browser_pid: browsers[0].id,
      browser_start: process.start,
    };
  }
  /** Pre-client-done phase: actual measured resources/mappings are already emitted.
   * DOM/resource/source-tail observers remain active until source-close. */
  async drainNativeTrace(command: NativeCommand, emit: Emit) {
    requireFact(this.terminalFailure === null, "failure-terminal-closed");
    requireFact(
      !this.traceDrained &&
        !this.traceDraining &&
        this.traceSession &&
        this.traceComplete &&
        this.jobs.size > 0 &&
        this.jobs.get(this.key(command))?.command === command &&
        this.jobs.get(this.key(command))?.emit === emit &&
        this.captureLocks.size === 0 &&
        [...this.jobs.values()].every((j) => j.measuredComplete || j.failed),
      "not-ready",
    );
    this.traceDraining = true;
    let failed = [...this.jobs.values()].some((j) => j.failed);
    const parser = new TraceMappings(),
      decoder = new StringDecoder("utf8"),
      retainedHash = createHash("sha256");
    const observation: Obj = {
      kind: "trace-completion",
      artifact_id: "native-trace",
      stream_id: null,
      end_dispatched_ns: this.clock.now().toString(),
      end_received_ns: null,
      complete_received_ns: null,
      eof_received_ns: null,
      data_loss: null,
      parser: "incomplete",
      observed_bytes: 0,
      retained_bytes: 0,
      retained_chunks: 0,
      retained_sha256: digest(Buffer.alloc(0)),
    };
    let parseFailed = false;
    try {
      await this.bounded(command, () => this.traceSession!.send("Tracing.end"));
      observation.end_received_ns = this.clock.now().toString();
      const complete = await this.bounded(command, () => this.traceComplete!);
      observation.complete_received_ns = complete.observed_ns;
      observation.data_loss =
        typeof complete.dataLossOccurred === "boolean"
          ? complete.dataLossOccurred
          : null;
      requireFact(
        typeof complete.stream === "string" && complete.stream.length > 0,
        "observer-loss",
      );
      observation.stream_id = complete.stream;
      failed ||= observation.data_loss !== false;
      for (;;) {
        const chunk = await this.bounded(command, () =>
          this.traceSession!.send("IO.read", {
            handle: complete.stream,
            size: LIMITS.chunk,
          }),
        );
        const raw = chunk.base64Encoded
          ? Buffer.from(chunk.data, "base64")
          : Buffer.from(chunk.data);
        observation.observed_bytes += raw.length;
        if (raw.length) {
          const retainedId = `trace-${observation.retained_chunks}`;
          this.hold(command, retainedId, raw);
          await this.send(
            command,
            emit,
            {
              kind: "private-chunk",
              artifact_id: "native-trace",
              purpose: "native-trace",
              chunk_index: observation.retained_chunks,
              data: raw.toString("base64"),
            },
            (sequence) =>
              this.recordAckSource(
                retainedId,
                {
                  kind: "native-record",
                  sequence,
                  purpose: "native-trace",
                  artifact_id: "native-trace",
                  chunk_index: observation.retained_chunks,
                  artifact_offset: 0,
                  bytes: raw.length,
                },
                raw,
              ),
          );
          this.release(retainedId);
          retainedHash.update(raw);
          observation.retained_bytes += raw.length;
          observation.retained_chunks++;
          try {
            requireFact(
              observation.observed_bytes <= LIMITS.shard,
              "observer-loss",
            );
            parser.push(decoder.write(raw));
          } catch {
            parseFailed = true;
          }
        }
        if (chunk.eof) {
          observation.eof_received_ns = this.clock.now().toString();
          break;
        }
      }
      try {
        parser.push(decoder.end());
        parser.end();
      } catch {
        parseFailed = true;
      }
      observation.parser = parseFailed ? "failed" : "complete";
      failed ||= parseFailed;
      await this.bounded(command, () =>
        this.traceSession!.send("IO.close", { handle: complete.stream }),
      );
    } catch {
      failed = true;
    }
    observation.retained_sha256 = retainedHash.digest("hex");
    this.traceFailed = failed;
    await this.send(command, emit, observation);
    for (const job of this.jobs.values()) {
      const { owned, command, emit } = job;
      const facts = await this.buildFacts(command, owned),
        frame = owned.document?.frameId;
      const pid = frame ? parser.pid(frame) : null;
      let renderer: Obj | null = null;
      if (pid !== null)
        try {
          renderer = await this.bounded(command, () =>
            this.inspectProcess(pid),
          );
          requireFact(
            owned.rendererCandidates.get(pid) === renderer.start,
            "renderer-binding",
          );
        } catch {
          failed = true;
        }
      if (renderer && pid !== null && frame && owned.document)
        owned.finalizedMapping = {
          frameId: frame,
          documentId: owned.document.uniqueId,
          pid,
          start: renderer.start,
        };
      const qualified = qualifyBuild(facts, REVIEWED_BUILDS);
      await this.send(command, emit, {
        kind: "capability",
        product: facts.product,
        revision: facts.revision,
        protocol_version: facts.protocol_version,
        executable_sha256: facts.executable_sha256,
        feature_enabled: facts.argv.some(
          (a: string) =>
            a.startsWith("--enable-features=") &&
            a.includes("CDPScreenshotNewSurface"),
        ),
        feature_conflict: facts.argv.some(
          (a: string) =>
            a.startsWith("--disable-features=") &&
            a.includes("CDPScreenshotNewSurface"),
        ),
        platform: facts.platform,
        browser_pid: facts.browser_pid,
        browser_start: facts.browser_start,
        renderer_pid: pid,
        renderer_start: renderer?.start ?? null,
        frame_id: frame ?? "missing",
        source_qualification_digest:
          qualified?.source_qualification_digest ?? null,
      });
      if (!qualified || !renderer) {
        failed = true;
        await this.send(command, emit, {
          kind: "error",
          code: !qualified ? "unsupported-native-build" : "renderer-binding",
          stage: "native-qualification",
          request_id: null,
          evidence_digest: null,
        });
      }
    }
    this.traceFailed = failed;
    this.traceDrained = true;
    this.traceDraining = false;
    requireFact(!failed, "native-trace-failed");
  }
  async finishNative() {
    requireFact(
      this.traceDrained &&
        this.busy.size === 0 &&
        [...this.controls.values()].every((s) => s.closed),
      "not-ready",
    );
    for (const job of this.jobs.values()) {
      const { command, emit } = job;
      await this.bounded(
        command,
        async () => this.outputs.get(this.key(command)),
        "output-queue",
      );
      await this.send(command, emit, {
        kind: "closed",
        outcome:
          job.failed || this.traceFailed ? "failed" : "observations-closed",
        records: (this.sequences.get(this.key(command)) ?? 0) + 1,
      });
    }
  }
}

async function processIdentity(pid: number): Promise<Obj> {
  requireFact(
    process.platform === "linux" && Number.isSafeInteger(pid) && pid > 0,
    "unsupported-native-build",
  );
  const stat = await readFile(`/proc/${pid}/stat`, "utf8"),
    start = stat.slice(stat.lastIndexOf(")") + 2).split(" ")[19];
  const executable = await readlink(`/proc/${pid}/exe`);
  const hash = createHash("sha256");
  for await (const bytes of createReadStream(`/proc/${pid}/exe`))
    hash.update(bytes);
  const again = await readFile(`/proc/${pid}/stat`, "utf8");
  requireFact(
    again.slice(again.lastIndexOf(")") + 2).split(" ")[19] === start &&
      (await readlink(`/proc/${pid}/exe`)) === executable,
    "renderer-binding",
  );
  return {
    platform: process.platform,
    start: `${await readlink(`/proc/${pid}/ns/pid`)}:${start}`,
    sha256: hash.digest("hex"),
  };
}

/** Incremental parsing of native trace events; original chunks remain private. */
export class TraceMappings {
  private text = "";
  private started = false;
  private done = false;
  private needComma = false;
  private hasEvent = false;
  private mappings = new Map<string, Set<number>>();
  push(chunk: string) {
    this.text += chunk;
    if (!this.started) {
      const match = /^\s*\{\s*"traceEvents"\s*:\s*\[/.exec(this.text);
      if (!match) {
        requireFact(this.text.length < 4096, "observer-loss");
        return;
      }
      this.text = this.text.slice(match.index + match[0].length);
      this.started = true;
    }
    requireFact(
      !this.done || Buffer.byteLength(this.text) < LIMITS.line,
      "observer-loss",
    );
    while (!this.done) {
      this.text = this.text.trimStart();
      if (!this.text) break;
      if (this.text.startsWith("]")) {
        requireFact(!this.hasEvent || this.needComma, "observer-loss");
        this.done = true;
        this.text = this.text.slice(1);
        break;
      }
      if (this.needComma) {
        requireFact(this.text[0] === ",", "observer-loss");
        this.text = this.text.slice(1).trimStart();
        this.needComma = false;
        if (!this.text) break;
      }

      requireFact(this.text[0] === "{", "observer-loss");
      let depth = 0,
        string = false,
        escape = false,
        end = -1;
      for (let i = 0; i < this.text.length; i++) {
        const c = this.text[i];
        if (string) {
          if (escape) escape = false;
          else if (c === "\\") escape = true;
          else if (c === '"') string = false;
        } else if (c === '"') string = true;
        else if (c === "{" || c === "[") depth++;
        else if (c === "}" || c === "]") {
          depth--;
          if (depth === 0) {
            end = i + 1;
            break;
          }
        }
      }
      if (end < 0) {
        requireFact(
          Buffer.byteLength(this.text) < LIMITS.line,
          "observer-loss",
        );
        break;
      }
      const event = JSON.parse(this.text.slice(0, end));
      this.text = this.text.slice(end);
      this.needComma = true;
      this.hasEvent = true;
      const data = event.args?.data;
      const rows =
        event.name === "TracingStartedInBrowser"
          ? data?.frames
          : ["FrameCommittedInBrowser", "ProcessReadyInBrowser"].includes(
                event.name,
              )
            ? [data]
            : [];
      for (const row of rows ?? [])
        if (
          typeof row.frame === "string" &&
          Number.isSafeInteger(row.processId) &&
          row.processId > 0
        ) {
          const set = this.mappings.get(row.frame) ?? new Set();
          set.add(row.processId);
          this.mappings.set(row.frame, set);
        }
      if (event.name === "FrameDeletedInBrowser" && data?.frame)
        this.mappings.delete(data.frame);
    }
  }
  end() {
    requireFact(this.started && this.done, "observer-loss");
    const tail = JSON.parse('{"traceEvents":[]' + this.text);
    requireFact(tail && Array.isArray(tail.traceEvents), "observer-loss");
  }
  pid(frame: string): number | null {
    const values = this.mappings.get(frame);
    return values?.size === 1 ? [...values][0] : null;
  }
}
