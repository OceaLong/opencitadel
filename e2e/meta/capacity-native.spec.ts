import { test, expect } from "@playwright/test";
import sharedWire from "../performance/native-wire-fixture-v1.json";
import {
  NativeLines,
  encodeFailureSnapshot,
  LIMITS,
  validateWire,
  FirstResponse,
  qualifyBuild,
  validatePNG,
} from "../performance/native-contract";

const intent = {
  operation: "first_screen",
  scope_id: "scope",
  session_id: "session",
  run_id: "run",
  step_id: null,
  batch_id: null,
  view: "task",
  history: null,
  filters: null,
  binding_rule: "first-authenticated-causal-response-v1",
};
export const command = {
  wire_version: 1,
  command: "collect",
  mode: "cold",
  attempt_id: "attempt",
  protocol_id: "protocol",
  sample_id: "sample",
  action_id: "action",
  context_id: "context",
  page_id: "page",
  window_id: "window",
  clock_id: "host",
  intent,
  deadline_ns: "100",
  window_open_ns: "10",
  resource_offset_ns: "1",
  resource_duration_ns: "20",
  require_positive_scroll: false,
  max_captures: 121,
  viewport_width: 1440,
  viewport_height: 900,
  collector_feature: "CDPScreenshotNewSurface",
};
const record = (sequence = 1) => ({
  ...Object.fromEntries(
    Object.entries(command).filter(([key]) =>
      [
        "wire_version",
        "attempt_id",
        "protocol_id",
        "sample_id",
        "action_id",
        "context_id",
        "page_id",
        "window_id",
        "clock_id",
      ].includes(key),
    ),
  ),
  sequence,
  received_ns: "12",
  observation: { kind: "closed", outcome: "failed", records: sequence },
});
test("wire rejects unknown executable, malformed values, partial EOF and duplicate sequence", () => {
  validateWire("command", command);
  expect(() =>
    validateWire("command", { ...command, executable: "/bin/sh" }),
  ).toThrow();
  expect(() =>
    validateWire("command", { ...command, viewport_width: true }),
  ).toThrow();
  const parser = new NativeLines(command);
  expect(
    parser.push(Buffer.from(JSON.stringify(record()) + "\n")),
  ).toHaveLength(1);
  expect(() =>
    parser.push(Buffer.from(JSON.stringify(record()) + "\n")),
  ).toThrow();
  expect(() => parser.end()).toThrow();
  const partial = new NativeLines(command);
  partial.push(Buffer.from('{"wire_version":'));
  expect(() => partial.end()).toThrow();
});
test("shared Python and TypeScript native fixture follows the original transport gate", () => {
  const parser = new NativeLines(sharedWire.command);
  for (const row of sharedWire.records)
    expect(parser.push(Buffer.from(JSON.stringify(row) + "\n"))).toHaveLength(
      1,
    );
  parser.end();
});
test("wire keeps valid Unicode and refuses isolated UTF-16 surrogates before JSON encoding", () => {
  const valid = { ...command, sample_id: "😀\t\u0000" };
  validateWire("command", valid);
  const validRow = { ...record(), sample_id: valid.sample_id };
  validateWire("record", validRow);
  expect(JSON.stringify(validRow)).toContain("😀");
  for (const invalid of ["\ud800", "\udc00", "x\ud800y", "\udc00😀"]) {
    expect(() =>
      validateWire("command", { ...command, sample_id: invalid }),
    ).toThrow("wire-unicode-scalar");
    expect(() =>
      validateWire("record", { ...record(), sample_id: invalid }),
    ).toThrow("wire-unicode-scalar");
    expect(() =>
      validateWire("command", {
        ...command,
        intent: { ...intent, scope_id: invalid },
      }),
    ).toThrow("wire-unicode-scalar");
  }
  expect(() =>
    validateWire("command", { ...command, ["\ud800"]: "x" }),
  ).toThrow("wire-unicode-scalar");
});
test("first response identity rejects old action, foreign scope and second matching response", () => {
  const gate = new FirstResponse(command, 10n);
  expect(() =>
    gate.accept({
      request_id: "old",
      response_id: "old",
      action_id: "action",
      request_ns: 9n,
      received_ns: 12n,
      scope_id: "scope",
      run_id: "run",
      public_id: "cut",
      revision: "5",
      step_id: null,
    }),
  ).toThrow();
  const fresh = new FirstResponse(command, 10n);
  const row = {
    request_id: "request",
    response_id: "response",
    action_id: "action",
    request_ns: 11n,
    received_ns: 12n,
    scope_id: "scope",
    run_id: "run",
    public_id: "cut",
    revision: "5",
    step_id: null,
  };
  expect(fresh.accept(row).target.public_id).toBe("cut");
  expect(() => fresh.accept(row)).toThrow();
  expect(() =>
    new FirstResponse(command, 10n).accept({ ...row, scope_id: "foreign" }),
  ).toThrow();
  expect(() =>
    new FirstResponse(command, 10n).accept({ ...row, action_id: "other" }),
  ).toThrow();
});
test("feature string alone and PNG header alone cannot qualify native capture", () => {
  expect(
    qualifyBuild(
      {
        product: "Chromium/151",
        revision: "unknown",
        executable_sha256: "a".repeat(64),
        platform: "linux",
        argv: ["--enable-features=CDPScreenshotNewSurface"],
      },
      [],
    ),
  ).toBeNull();
  expect(() => validatePNG(Buffer.from("89504e470d0a1a0a", "hex"))).toThrow();
});

import { deflateSync, inflateSync } from "node:zlib";
import { cropProgressPNG, digest } from "../performance/native-contract";
import {
  NativeCollector,
  TraceMappings,
  type OwnedContext,
} from "../performance/native-collector";
function testPNG(filter = 0) {
  const crc = (b: Buffer) => {
    let c = 0xffffffff;
    for (const x of b) {
      c ^= x;
      for (let i = 0; i < 8; i++) c = (c >>> 1) ^ (c & 1 ? 0xedb88320 : 0);
    }
    return (c ^ 0xffffffff) >>> 0;
  };
  const chunk = (name: string, data: Buffer) => {
    const b = Buffer.alloc(data.length + 12);
    b.writeUInt32BE(data.length, 0);
    b.write(name, 4);
    data.copy(b, 8);
    b.writeUInt32BE(crc(b.subarray(4, 8 + data.length)), 8 + data.length);
    return b;
  };
  const head = Buffer.alloc(13);
  head.writeUInt32BE(1440, 0);
  head.writeUInt32BE(900, 4);
  head[8] = 8;
  head[9] = 2;
  const raw = Buffer.alloc(900 * (1440 * 3 + 1));
  for (let y = 0; y < 900; y++)
    for (let x = 0; x < 1440; x++) {
      const i = y * (1440 * 3 + 1) + 1 + x * 3;
      raw[i] = x % 256;
      raw[i + 1] = y % 256;
      raw[i + 2] = 77;
    }
  if (filter)
    for (let y = 899; y >= 0; y--) {
      const stride = 1440 * 3 + 1;
      raw[y * stride] = filter;
      for (let x = 1440 * 3 - 1; x >= 0; x--) {
        const at = y * stride + 1 + x,
          a = x >= 3 ? raw[at - 3] : 0,
          b = y ? raw[at - stride] : 0,
          c = y && x >= 3 ? raw[at - stride - 3] : 0;
        const p = a + b - c,
          pa = Math.abs(p - a),
          pb = Math.abs(p - b),
          pc = Math.abs(p - c);
        const prediction =
          filter === 1
            ? a
            : filter === 2
              ? b
              : filter === 3
                ? Math.floor((a + b) / 2)
                : pa <= pb && pa <= pc
                  ? a
                  : pb <= pc
                    ? b
                    : c;
        raw[at] = (raw[at] - prediction + 256) % 256;
      }
    }
  return Buffer.concat([
    Buffer.from("89504e470d0a1a0a", "hex"),
    chunk("IHDR", head),
    chunk("IDAT", deflateSync(raw)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}
test("lossless crop uses outward text hull plus four pixels and preserves known pixels", () => {
  const reference = validatePNG(testPNG()).pixels;
  for (let filter = 1; filter <= 4; filter++)
    expect(validatePNG(testPNG(filter)).pixels).toEqual(reference);
  const full = testPNG(),
    originalHash = digest(full);
  const result = cropProgressPNG(full, [
    [20.5, 30.5, 100, 18],
    [21, 31, 98, 17],
  ]);
  expect(result.rect).toEqual([16, 26, 109, 27]);
  expect(digest(full)).toBe(originalHash);
  const idatLength = result.bytes.readUInt32BE(33);
  const pixels = inflateSync(result.bytes.subarray(41, 41 + idatLength));
  expect([...pixels.subarray(1, 4)]).toEqual([16, 26, 77]);
  expect(result.bytes.length).toBeLessThanOrEqual(96 * 1024);
  expect(() => cropProgressPNG(full, [[1, 1, 50, 20]])).toThrow(
    "crop-geometry",
  );
  expect(() => cropProgressPNG(full, [[10, 10, 400, 20]])).toThrow(
    "crop-geometry",
  );
  const bad = Buffer.from(full);
  bad[50] ^= 1;
  expect(() => validatePNG(bad)).toThrow("png-crc");
});
test("qualified build requires exact actual binary/source match and no feature conflict", () => {
  const entry = {
    product: "Chrome/qualified",
    revision: "source",
    platform: "linux" as const,
    executable_sha256: "a".repeat(64),
    source_qualification_digest: "b".repeat(64),
  };
  const facts = {
    ...entry,
    argv: ["--enable-features=Other,CDPScreenshotNewSurface"],
  };
  expect(qualifyBuild(facts, [entry])).toEqual(entry);
  expect(
    qualifyBuild(
      {
        ...facts,
        argv: [...facts.argv, "--disable-features=CDPScreenshotNewSurface"],
      },
      [entry],
    ),
  ).toBeNull();
  expect(
    qualifyBuild({ ...facts, executable_sha256: "c".repeat(64) }, [entry]),
  ).toBeNull();
});
test("trace mapping consumes complete native event fields, rejects PID ambiguity and EOF", () => {
  const parser = new TraceMappings();
  const raw = JSON.stringify({
    traceEvents: [
      {
        name: "TracingStartedInBrowser",
        args: { data: { frames: [{ frame: "f", processId: 123 }] } },
      },
    ],
  });
  for (let i = 0; i < raw.length; i += 7) parser.push(raw.slice(i, i + 7));
  parser.end();
  expect(parser.pid("f")).toBe(123);
  const ambiguous = new TraceMappings();
  ambiguous.push(
    JSON.stringify({
      traceEvents: [
        {
          name: "TracingStartedInBrowser",
          args: {
            data: {
              frames: [
                { frame: "f", processId: 1 },
                { frame: "f", processId: 2 },
              ],
            },
          },
        },
      ],
    }),
  );
  ambiguous.end();
  expect(ambiguous.pid("f")).toBeNull();
  for (const invalid of [
    '{"traceEvents":[]',
    '{"traceEvents":[{},]}',
    '{"traceEvents":[{}{}]}',
    '{"traceEvents":[]}garbage',
  ]) {
    expect(() => {
      const broken = new TraceMappings();
      broken.push(invalid);
      broken.end();
    }).toThrow();
  }
  const partial = new TraceMappings();
  partial.push('{"traceEvents":[{');
  expect(() => partial.end()).toThrow();
});
test("real capture adapter preserves full original on transient mutation and pending qualification", async () => {
  const commandForCapture = { ...command, deadline_ns: "1000000" };
  const bytes = testPNG();
  const rows: any[] = [];
  let mutated = false;
  let now = 20n;
  const read = {
    target: {
      scope_id: "scope",
      run_id: "run",
      public_id: "cut",
      revision: "5",
      step_id: null,
    },
    owners: [
      {
        serial: 1,
        content: "summary",
        text: "Actual goal",
        identifier: "execution-summary",
        run_id: null,
        step_id: null,
        revision: null,
        result_id: null,
        rect: [20, 20, 100, 20],
        native_rect: [20, 20, 100, 20],
        text_rects: [[20, 20, 100, 20]],
        render_time_ms: 1,
      },
    ],
    mutation_sequence: 0,
    visible: true,
    document_id: "doc",
    frame_id: "frame",
    execution_context_id: "doc",
    target_id: "target",
  };
  const calls: string[] = [];
  const owned = {
    targetId: "target",
    document: { frameId: "frame", uniqueId: "doc" },
    rendererCandidates: new Map(),
    cdp: {
      send: async (name: string, args: any) => {
        calls.push(name);
        if (name === "Page.captureScreenshot") {
          expect(args).toEqual({
            format: "png",
            fromSurface: true,
            captureBeyondViewport: false,
          });
          return { data: bytes.toString("base64") };
        }
        return {
          result: {
            value: {
              dataset: read.target,
              owners: read.owners,
              visible: true,
              mutation_sequence: mutated ? 2 : 0,
            },
          },
        };
      },
    },
    browserCDP: {
      send: async (name: string) =>
        name === "SystemInfo.getProcessInfo"
          ? {
              processInfo: [
                { id: 10, type: "browser" },
                { id: 20, type: "renderer" },
              ],
            }
          : name === "Browser.getVersion"
            ? {
                product: "Chrome/unqualified",
                revision: "unknown",
                protocolVersion: "1.3",
              }
            : { arguments: ["--enable-features=CDPScreenshotNewSurface"] },
    },
  } as unknown as OwnedContext;
  const collector = new NativeCollector(
    {} as any,
    {} as any,
    { now: () => now++, wait: () => new Promise(() => {}) },
    new AbortController().signal,
    async () => ({
      platform: "linux",
      start: "namespace:100",
      sha256: "a".repeat(64),
    }),
  );
  const emit = async (line: Buffer) => {
    const row = JSON.parse(line.toString());
    rows.push(row);
    return row.sequence;
  };
  await (collector as any).send(commandForCapture, emit, {
    kind: "readback",
    ...read,
  });
  mutated = true;
  await expect(
    collector.captureNative(commandForCapture, owned, read, "capture-1", emit),
  ).rejects.toThrow("unstable-content");
  const rejected = Buffer.concat(
    rows
      .filter((r) => r.observation.kind === "private-chunk")
      .map((r) => Buffer.from(r.observation.data, "base64")),
  );
  expect(rejected).toEqual(bytes);
  mutated = false;
  await expect(
    collector.captureNative(commandForCapture, owned, read, "capture-2", emit),
  ).rejects.toThrow("unsupported-native-build");
  expect(rows.at(-1).observation.qualification).toBe(
    "pending-runtime-qualification",
  );
  expect(calls.filter((x) => x === "Page.captureScreenshot")).toHaveLength(2);
});

// Connected source graph: DOM and CDP boundaries are injected; no Browser launch.
import { EventEmitter } from "node:events";
const { JSDOM } = require("../../ui/node_modules/jsdom");
const contractModule = require("../performance/native-contract");
const registered = {
  product: "Chrome/qualified",
  revision: "source",
  platform: "linux",
  executable_sha256: "a".repeat(64),
  source_qualification_digest: "b".repeat(64),
};
function nativeGraph(operation = "live_visible", defect = "") {
  let ns = 1000000n,
    init: Function,
    rendered = false,
    captureCount = 0,
    readCount = 0;
  let collectedFailure: unknown;
  let drain: Promise<unknown> | undefined,
    collect: Promise<unknown> | undefined;
  const abort = new AbortController(),
    rows: any[] = [],
    actions: any[] = [],
    timers: Array<() => void> = [];
  const c: any = {
    ...command,
    mode: operation === "switch" ? "warm" : "cold",
    intent: {
      ...intent,
      operation,
      step_id: ["switch", "live_visible"].includes(operation) ? "step" : null,
      history:
        operation === "history"
          ? { direction: "previous", anchor_at: "base", target_time: null }
          : null,
    },
    deadline_ns: "10000000000",
    window_open_ns: operation === "live_visible" ? null : "1",
    resource_offset_ns: "200000000",
    resource_duration_ns: "100000000",
  };
  const dom = new JSDOM("<main></main>", {
      url: "https://app.invalid",
      runScripts: "outside-only",
    }),
    w = dom.window;
  Object.defineProperties(w, {
    innerWidth: { value: 1440 },
    innerHeight: { value: 900 },
  });
  Object.defineProperty(w.document, "visibilityState", {
    get: () => "visible",
  });
  w.getComputedStyle = () => ({
    display: "block",
    visibility: "visible",
    opacity: "1",
    filter: "none",
    transform: "none",
    clipPath: "none",
  });
  const rect = {
    x: 20,
    y: 20,
    width: 200,
    height: 20,
    left: 20,
    top: 20,
    right: 220,
    bottom: 40,
  };
  w.HTMLElement.prototype.getBoundingClientRect = () => rect;
  w.Range.prototype.getClientRects = () => [rect];
  w.document.elementFromPoint = () =>
    w.document.querySelector("[data-native-content]")?.parentElement;
  Object.defineProperties(w.HTMLElement.prototype, {
    scrollHeight: { get: () => 100 },
    clientHeight: { get: () => 100 },
  });
  let elementObserver: Function = () => {};
  w.PerformanceObserver = class {
    static supportedEntryTypes = ["element", "longtask"];
    constructor(private cb: Function) {}
    observe({ type }: any) {
      if (type === "element") elementObserver = this.cb;
    }
  };
  w.requestAnimationFrame = () => 0;
  w.document.getAnimations = () => [];
  const frame = {},
    page: any = new EventEmitter(),
    cdp: any = new EventEmitter(),
    browserCDP: any = new EventEmitter();
  const pulse = () => new Promise<void>((resolve) => setImmediate(resolve));
  const paint = async () => {
    await pulse();
    const elements = [
      ...w.document.querySelectorAll("[elementtiming]"),
    ] as any[];
    elementObserver({
      getEntries: () =>
        elements.map((e) => ({
          element: e,
          name: "text-paint",
          identifier: e.getAttribute("elementtiming"),
          renderTime: 1,
          intersectionRect: rect,
        })),
    });
  };
  const render = async () => {
    if (rendered) return;
    rendered = true;
    const kind = operation === "switch" ? "detail" : "task",
      actualStep = defect === "dom-step" ? "foreign" : "step";
    const contents =
      operation === "switch"
        ? '<span elementtiming="identity" data-native-content="detail-identity">Actual step</span><span elementtiming="status" data-native-content="detail-status">Running</span>'
        : operation === "live_visible"
          ? '<span elementtiming="progress" data-native-content="progress" data-public-step="step" data-public-run="run" data-public-revision="5">Received fragments: 1</span>'
          : '<span elementtiming="summary" data-native-content="summary">Actual summary</span>';
    w.document.querySelector("main").innerHTML =
      `<section data-native-view="${kind}" data-native-ready="true" data-public-scope="scope" data-public-run="run" data-public-at="cut" data-public-revision="5" data-public-step="${actualStep}">${contents}</section>`;
    await paint();
  };
  const respond = async (path: string, data: any) => {
    const request = {
      frame: () => frame,
      allHeaders: async () => ({ "x-workspace-id": "scope" }),
    };
    page.emit("request", request);
    page.emit("response", {
      request: () => request,
      url: () => `https://app.invalid${path}`,
      status: () => 200,
      json: async () => ({ code: 200, data }),
    });
    await pulse();
  };
  const viewResponse = async () =>
    respond(
      operation === "switch"
        ? "/api/execution-runs/run/steps/step"
        : "/api/execution-runs/run/view",
      {
        run_id: "run",
        run: { run_id: "run" },
        at: "cut",
        revision: 5,
        step_id: defect === "response-step" ? "foreign" : "step",
        steps: [{ step_id: "step" }],
      },
    );
  const eventStream = () => {
    cdp.emit("Network.requestWillBeSent", {
      requestId: "sse",
      frameId: "frame",
      request: {
        url: "https://app.invalid/api/execution-runs/run/events/stream",
        headers: { "x-workspace-id": "scope", "last-event-id": "initial" },
      },
    });
    cdp.emit("Network.responseReceived", {
      requestId: "sse",
      response: { status: 200, mimeType: "text/event-stream" },
    });
  };
  page.mainFrame = () => frame;
  page.goto = async (url: string) => {
    actions.push({ kind: "goto", url, ns: ns.toString() });
    if (url.startsWith("/sessions/")) {
      await render();
      if (operation !== "switch" && operation !== "history")
        await viewResponse();
      if (operation === "live_visible") eventStream();
    }
  };
  page.evaluate = async (fn: Function, arg: any) => {
    if (arg?.requestPath)
      return {
        status: 200,
        payload: {
          code: 200,
          msg: "ok",
          data: { id: "principal", status: "active" },
        },
      };
    return w.eval(`(${fn.toString()})`)(arg);
  };
  const locator: any = {
    first() {
      return this;
    },
    nth() {
      return this;
    },
    locator() {
      return this;
    },
    waitFor: async () => {},
    boundingBox: async () => rect,
    evaluate: async (fn: Function) =>
      fn(
        w.document.querySelector("[data-native-view]") ??
          w.document.querySelector("main"),
      ),
    click: async () => {
      actions.push({ kind: "click", ns: ns.toString() });
      if (operation === "history") {
        await respond(
          "/api/execution-runs/run/timeline?direction=before&anchor_at=base",
          {
            at: "cut",
            run_id: "run",
          },
        );
        await respond("/api/execution-runs/run/view?at=cut", {
          run_id: "run",
          at: "cut",
          revision: 5,
        });
      } else await viewResponse();
    },
  };
  page.locator = () => locator;
  page.getByRole = () => locator;
  page.getByTestId = () => locator;
  page.mouse = { move: async () => {}, wheel: async () => {} };
  const png = testPNG();
  let resolveRead: ((v: any) => void) | undefined;
  let resolveCapture: ((v: any) => void) | undefined;
  cdp.send = async (name: string, args: any) => {
    ns += 1000n;
    if (name === "Runtime.enable")
      cdp.emit("Runtime.executionContextCreated", {
        context: {
          id: 1,
          uniqueId: "document",
          auxData: { isDefault: true, frameId: "frame" },
        },
      });
    if (name === "Target.getTargetInfo")
      return { targetInfo: { targetId: "target" } };
    if (name === "Network.streamResourceContent")
      return {
        bufferedData: Buffer.from(
          'event: execution\ndata: {"run_id":"run","cursor":"after","event_id":"event"}\n\n',
        ).toString("base64"),
      };
    if (name === "Runtime.getHeapUsage") return { usedSize: 1000 };
    if (name === "Runtime.evaluate") {
      readCount++;
      if (defect === "hung-postcheck" && captureCount > 0)
        return new Promise((resolve) => {
          resolveRead = resolve;
        });
      try {
        return { result: { value: w.eval(args.expression) } };
      } catch (e) {
        return { exceptionDetails: {}, result: { description: String(e) } };
      }
    }
    if (name === "Page.captureScreenshot") {
      captureCount++;
      if (defect === "late-capture")
        return new Promise((resolve) => {
          resolveCapture = resolve;
        });
      if (defect === "portal") {
        const overlay = w.document.createElement("aside");
        w.document.body.append(overlay);
        overlay.remove();
      }
      if (defect === "ancestor") {
        w.document.body.setAttribute("style", "opacity:0");
        w.document.body.removeAttribute("style");
      }
      return { data: png.toString("base64") };
    }
    return {};
  };
  browserCDP.send = async (name: string) => {
    ns += 1000n;
    if (name === "Browser.getVersion")
      return { ...registered, protocolVersion: "1.3" };
    if (name === "Browser.getBrowserCommandLine")
      return { arguments: ["--enable-features=CDPScreenshotNewSurface"] };
    if (name === "SystemInfo.getProcessInfo")
      return {
        processInfo: [
          { id: 10, type: "browser" },
          { id: 20, type: "renderer" },
        ],
      };
    if (name === "Tracing.end") {
      browserCDP.emit("Tracing.tracingComplete", {
        stream: "trace-stream",
        dataLossOccurred: defect === "trace-loss",
      });
      return {};
    }
    if (name === "IO.read")
      return {
        data: JSON.stringify({
          traceEvents: [
            {
              name: "TracingStartedInBrowser",
              args: { data: { frames: [{ frame: "frame", processId: 20 }] } },
            },
          ],
        }),
        eof: true,
      };
    return {};
  };
  const context: any = {
    exposeBinding: async () => {},
    addInitScript: async (fn: Function) => {
      init = fn;
      w.eval(`(${init.toString()})()`);
    },
    newPage: async () => page,
    newCDPSession: async () => cdp,
    close: async () => {},
  };
  const browser: any = {
    newContext: async () => context,
    newBrowserCDPSession: async () => browserCDP,
  };
  const clock = {
    now: () => ns++,
    wait: (ms: number, signal: AbortSignal) =>
      new Promise<void>((resolve, reject) => {
        const cancel = () => reject(new Error("cancelled"));
        signal.addEventListener("abort", cancel, { once: true });
        if (ms === 50)
          setImmediate(() => {
            signal.removeEventListener("abort", cancel);
            ns += 50000000n;
            resolve();
          });
        else
          timers.push(() => {
            ns = BigInt(c.deadline_ns);
            resolve();
          });
      }),
  };
  const collector = new NativeCollector(
    browser,
    {
      origin: "https://app.invalid",
      principalId: "principal",
      workspaceId: "scope",
      credentials: { email_or_username: "user", password: "private" },
    },
    clock,
    abort.signal,
    async () => ({
      platform: "linux",
      start: "pidns:1",
      sha256: "a".repeat(64),
    }),
  );
  let controlSequence = 0;
  const control = (kind: string, opened?: string) =>
    collector.acceptControl(c, {
      ...Object.fromEntries(
        Object.entries(c).filter(([k]) =>
          [
            "wire_version",
            "attempt_id",
            "protocol_id",
            "sample_id",
            "action_id",
            "context_id",
            "page_id",
            "window_id",
            "clock_id",
          ].includes(k),
        ),
      ),
      command: kind,
      sequence: ++controlSequence,
      source_receipt_id: `receipt-${kind}`,
      progress: null,
      opened_ns: opened ?? null,
    });
  const emit = async (line: Buffer) => {
    const row = JSON.parse(line.toString());
    rows.push(row);
    if (
      row.observation.kind === "ready" &&
      operation === "live_visible" &&
      defect !== "manual-open"
    )
      setImmediate(() => control("open", ns.toString()));
    return row.sequence;
  };
  return {
    c,
    collector,
    rows,
    actions,
    png,
    abort,
    control,
    timers,
    get readCount() {
      return readCount;
    },
    get captureCount() {
      return captureCount;
    },
    resolveCapture: () => resolveCapture?.({ data: png.toString("base64") }),
    resolveRead: () =>
      resolveRead?.({
        result: { value: w.__capacityNative.read(operation, c.intent.step_id) },
      }),
    async start() {
      const owned = await collector.openNativeContext(c);
      if (operation === "switch") await collector.prepareNative(c, owned, emit);
      collect = collector.collectNative(c, owned, emit);
      collect.catch((e) => {
        collectedFailure = e;
      });
      return collect;
    },
    async complete() {
      while (
        !rows.some(
          (r) =>
            r.observation.kind === "resource-boundary" &&
            r.observation.phase === "end",
        )
      ) {
        if (collectedFailure) throw collectedFailure;
        await pulse();
      }
      await pulse();
      drain = collector.drainNativeTrace(c, emit);
      try {
        await drain;
      } finally {
        control("close");
      }
      await collect;
      await collector.finishNative();
    },
    dispose() {
      w.close();
    },
    pulse,
    now: () => ns.toString(),
  };
}
async function withQualifiedGraph(
  operation: string,
  defect: string,
  run: (g: ReturnType<typeof nativeGraph>) => Promise<void>,
) {
  const prior = contractModule.REVIEWED_BUILDS;
  // Substitute the reviewed qualification boundary only in this pure process.
  contractModule.REVIEWED_BUILDS = [registered];
  const graph = nativeGraph(operation, defect);
  try {
    await run(graph);
  } finally {
    graph.dispose();
    contractModule.REVIEWED_BUILDS = prior;
  }
}
test("connected live ready then observed open then measured resources and complete trace", async () => {
  await withQualifiedGraph("live_visible", "", async (g) => {
    const work = g.start();
    work.catch(() => {});
    await g.complete();
    await work;
    const kinds = g.rows.map((r) => r.observation.kind);
    expect(kinds.indexOf("ready")).toBeLessThan(kinds.indexOf("open"));
    expect(
      g.rows.find((r) => r.observation.kind === "trace-completion").observation,
    ).toMatchObject({ parser: "complete", data_loss: false });
    expect(g.rows.at(-1).observation.outcome).toBe("observations-closed");
  });
});
test("connected complete JSON with native data loss fails before client-done", async () => {
  await withQualifiedGraph("switch", "trace-loss", async (g) => {
    const work = g.start();
    work.catch(() => {});
    await expect(g.complete()).rejects.toThrow("native-trace-failed");
    await work;
    expect(
      g.rows.find((r) => r.observation.kind === "trace-completion").observation,
    ).toMatchObject({ parser: "complete", data_loss: true });
    expect(g.rows.some((r) => r.observation.kind === "private-chunk")).toBe(
      true,
    );
  });
});
for (const defect of ["response-step", "dom-step"])
  test(`connected selected step rejects ${defect}`, async () => {
    await withQualifiedGraph("switch", defect, async (g) => {
      await expect(g.start()).rejects.toThrow("response-binding");
      expect(g.rows.some((r) => r.observation.kind === "capture")).toBe(false);
    });
  });
for (const defect of ["portal", "ancestor"])
  test(`actual observer rejects transient ${defect} and retains full PNG`, async () => {
    await withQualifiedGraph("switch", defect, async (g) => {
      await expect(g.start()).rejects.toThrow("unstable-content");
      const before = g.rows.find((r) => r.observation.kind === "readback"),
        after = g.rows.find((r) => r.observation.kind === "postcheck");
      expect(after.observation.mutation_sequence).toBeGreaterThan(
        before.observation.mutation_sequence,
      );
      expect(
        Buffer.concat(
          g.rows
            .filter((r) => r.observation.purpose === "rejected-capture")
            .map((r) => Buffer.from(r.observation.data, "base64")),
        ),
      ).toEqual(g.png);
    });
  });
for (const operation of ["history", "switch"])
  test(`connected ${operation} emits actual trigger before earliest navigation/click`, async () => {
    await withQualifiedGraph(operation, "", async (g) => {
      const work = g.start();
      work.catch(() => {});
      await g.complete();
      await work;
      const trigger = g.rows.find((r) => r.observation.kind === "action")
        .observation.trigger_ns;
      const actual = g.actions.find((a) =>
        operation === "history"
          ? a.kind === "goto" && a.url.startsWith("/sessions")
          : a.kind === "click",
      );
      expect(BigInt(trigger)).toBeLessThanOrEqual(BigInt(actual.ns));
      expect(
        BigInt(
          g.rows.find((r) => r.observation.kind === "binding").observation
            .binding.request_ns,
        ),
      ).toBeGreaterThanOrEqual(BigInt(actual.ns));
    });
  });

for (const reason of ["cancelled", "deadline", "control-cancel"])
  test(`connected hung postcheck ${reason} owns full PNG and late result until durable failure ACK`, async () => {
    await withQualifiedGraph("switch", "hung-postcheck", async (g) => {
      const work = g.start();
      work.catch(() => {});
      while (g.readCount < 2) await g.pulse();
      if (reason === "cancelled") g.abort.abort();
      else if (reason === "control-cancel") g.control("cancel");
      else g.timers.forEach((f) => f());
      await expect(work).rejects.toThrow(
        reason === "control-cancel" ? "cancelled" : reason,
      );
      const state = g.collector.failureState(g.c);
      expect(state.disposition).toBe("pending");
      expect(
        state.operations.some(
          (o) => o.state === "pending" && o.stage === "content-readback",
        ),
      ).toBe(true);
      expect(
        state.artifacts.find((a) => a.artifact_id.startsWith("png-")),
      ).toMatchObject({ sha256: digest(g.png), retained_bytes: g.png.length });
      g.resolveRead();
      await g.pulse();
      const saved: Buffer[] = [];
      const settled = await g.collector.drainFailureEvidence(
        g.c,
        async (chunk) => {
          if (chunk.artifact_id.startsWith("png-"))
            saved.push(Buffer.from(chunk.data));
          return {
            ...chunk,
            durable_receipt_id: `durable-${chunk.artifact_id}-${chunk.offset}`,
          };
        },
        new AbortController().signal,
      );
      expect(Buffer.concat(saved)).toEqual(g.png);
      expect(settled.held_bytes).toBe(0);
      expect(settled.disposition).toBe("settled");
      expect(settled.wire_version).toBe(2);
      expect(settled.artifacts[0]).toMatchObject({
        retained_bytes: g.png.length,
        acknowledged_bytes: g.png.length,
        sha256: digest(g.png),
        source_record_refs: [],
      });
      expect(g.rows.some((r) => r.observation.kind === "capture")).toBe(false);
    });
  });
test("connected late screenshot remains owned after cancellation; blocked failure ACK remains pending", async () => {
  await withQualifiedGraph("switch", "late-capture", async (g) => {
    const work = g.start();
    work.catch(() => {});
    while (g.captureCount < 1) await g.pulse();
    g.abort.abort();
    await expect(work).rejects.toThrow("cancelled");
    expect(
      g.collector
        .failureState(g.c)
        .operations.some(
          (o) => o.state === "pending" && o.stage === "native-screenshot",
        ),
    ).toBe(true);
    g.resolveCapture();
    await g.pulse();
    expect(g.collector.failureState(g.c).artifacts[0].sha256).toBe(
      digest(g.png),
    );
    const cleanup = new AbortController();
    let ack: ((v: any) => void) | undefined, chunk: any;
    const draining = g.collector.drainFailureEvidence(
      g.c,
      (c) => {
        chunk = c;
        return new Promise((resolve) => {
          ack = resolve;
        });
      },
      cleanup.signal,
    );
    await g.pulse();
    cleanup.abort();
    const pending = await draining;
    expect(pending.disposition).toBe("pending");
    expect(pending.held_bytes).toBe(g.png.length);
    ack!({ ...chunk, durable_receipt_id: "late-durable" });
    await g.pulse();
    expect(
      g.collector
        .failureState(g.c)
        .operations.some((o) => o.state === "pending"),
    ).toBe(false);
    // A late ACK releases only its exact acknowledged chunk, never the remainder.
    expect(g.collector.failureState(g.c).held_bytes).toBe(
      g.png.length - chunk.bytes,
    );
    const remaining = g.collector.failureState(g.c).artifacts[0];
    if (remaining) expect(remaining.acknowledged_bytes).toBe(chunk.bytes);
  });
});
test("terminal failure barrier waits for reordered durable ACK callbacks and freezes partial state", async () => {
  await withQualifiedGraph("switch", "late-capture", async (g) => {
    const work = g.start();
    work.catch(() => {});
    while (g.captureCount < 1) await g.pulse();
    g.abort.abort();
    await expect(work).rejects.toThrow("cancelled");
    expect(() => g.collector.terminalFailureSnapshotBytes(g.c)).toThrow(
      "failure-terminal-not-ready",
    );
    g.resolveCapture();
    await g.pulse();

    const callbacks: Array<(receipt: any) => void> = [];
    const chunks: any[] = [];
    for (let i = 0; i < 2; i++) {
      const cleanup = new AbortController();
      const draining = g.collector.drainFailureEvidence(
        g.c,
        (chunk) => {
          chunks.push(chunk);
          return new Promise((resolve) => callbacks.push(resolve));
        },
        cleanup.signal,
      );
      await g.pulse();
      expect(callbacks).toHaveLength(i + 1);
      expect(() => g.collector.terminalFailureSnapshotBytes(g.c)).toThrow(
        "failure-terminal-not-ready",
      );
      cleanup.abort();
      expect((await draining).disposition).toBe("pending");
      expect(() => g.collector.terminalFailureSnapshotBytes(g.c)).toThrow(
        "failure-terminal-not-ready",
      );
    }
    expect(chunks).toHaveLength(2);
    expect(chunks[0].offset).toBe(0);
    expect(chunks[1].offset).toBe(0);
    expect(() => g.collector.failureSnapshotBytes(g.c)).not.toThrow();

    callbacks[1]({ ...chunks[1], durable_receipt_id: "second-durable" });
    await g.pulse();
    expect(() => g.collector.terminalFailureSnapshotBytes(g.c)).toThrow(
      "failure-terminal-not-ready",
    );
    callbacks[0]({ ...chunks[0], durable_receipt_id: "first-stale" });
    await g.pulse();
    const before = g.collector.failureState(g.c);
    expect(before.operations.some((o) => o.state === "pending")).toBe(false);
    expect(before.operations.some((o) => o.state === "rejected")).toBe(true);
    expect(before.artifacts[0].acknowledged_bytes).toBe(chunks[1].bytes);
    expect(before.disposition).toBe("partial");
    const remaining = g.collector.readFailureChunk(
      g.c,
      before.artifacts[0].artifact_id,
    );
    const terminal = g.collector.terminalFailureSnapshotBytes(g.c);
    expect(JSON.parse(terminal.toString("utf8"))).toMatchObject({
      disposition: "partial",
      held_bytes: g.png.length - chunks[1].bytes,
    });
    terminal[0] = 0;
    const frozen = g.collector.terminalFailureSnapshotBytes(g.c);
    expect(frozen[0]).toBe("{".charCodeAt(0));
    expect(() =>
      g.collector.acknowledgeFailureChunk(g.c, {
        ...remaining,
        durable_receipt_id: "after-terminal",
      }),
    ).toThrow("failure-terminal-closed");
    await expect(
      g.collector.drainFailureEvidence(
        g.c,
        async (chunk) => ({ ...chunk, durable_receipt_id: "too-late" }),
        new AbortController().signal,
      ),
    ).rejects.toThrow("failure-terminal-closed");
    expect(() =>
      (g.collector as any).hold(g.c, "after-terminal", Buffer.from("x")),
    ).toThrow("failure-terminal-closed");
    let writes = 0;
    await expect(
      (g.collector as any).send(
        g.c,
        async () => {
          writes++;
          return 1;
        },
        { kind: "closed" },
      ),
    ).rejects.toThrow("failure-terminal-closed");
    expect(writes).toBe(0);
    const second = {
      ...g.c,
      sample_id: "second-sample",
      context_id: "second-context",
      page_id: "second-page",
    };
    await expect(
      g.collector.collectNative(
        second,
        { contextId: second.context_id, pageId: second.page_id } as any,
        async () => 1,
      ),
    ).rejects.toThrow("failure-terminal-closed");
    await expect(
      g.collector.prepareNative(g.c, {} as any, async () => 1),
    ).rejects.toThrow("failure-terminal-closed");
    await expect(
      g.collector.captureNative(g.c, {} as any, {}, "late", async () => 1),
    ).rejects.toThrow("failure-terminal-closed");
    await expect(
      g.collector.drainNativeTrace(g.c, async () => 1),
    ).rejects.toThrow("failure-terminal-closed");
    expect(g.collector.terminalFailureSnapshotBytes(g.c)).toEqual(frozen);
    expect(g.collector.failureState(g.c).held_bytes).toBe(before.held_bytes);
  });
});
test("terminal failure barrier commits an already settled fully ACKed snapshot", async () => {
  await withQualifiedGraph("switch", "late-capture", async (g) => {
    const work = g.start();
    work.catch(() => {});
    while (g.captureCount < 1) await g.pulse();
    g.abort.abort();
    await expect(work).rejects.toThrow("cancelled");
    g.resolveCapture();
    await g.pulse();
    const settled = await g.collector.drainFailureEvidence(
      g.c,
      async (chunk) => ({
        ...chunk,
        durable_receipt_id: `host-${chunk.artifact_id}-${chunk.offset}`,
      }),
      new AbortController().signal,
    );
    expect(settled.disposition).toBe("settled");
    const terminal = g.collector.terminalFailureSnapshotBytes(g.c);
    expect(JSON.parse(terminal.toString("utf8"))).toMatchObject({
      disposition: "settled",
      held_bytes: 0,
      artifacts: [
        {
          acknowledged_bytes: g.png.length,
          retained_bytes: g.png.length,
        },
      ],
    });
    expect(g.collector.terminalFailureSnapshotBytes(g.c)).toEqual(terminal);
    expect(() =>
      g.collector.terminalFailureSnapshotBytes({
        ...g.c,
        sample_id: "foreign",
      }),
    ).toThrow("foreign-failure-snapshot");
  });
});
test("failure ownership keeps cumulative artifact identities after every durable release", async () => {
  await withQualifiedGraph("switch", "late-capture", async (g) => {
    const work = g.start();
    work.catch(() => {});
    while (g.captureCount < 1) await g.pulse();
    g.abort.abort();
    await expect(work).rejects.toThrow("cancelled");
    g.resolveCapture();
    await g.pulse();
    const first = g.collector.failureState(g.c).artifacts[0].artifact_id;
    const release = (id: string) => {
      while (
        g.collector
          .failureState(g.c)
          .artifacts.some(
            (a) =>
              a.artifact_id === id && a.acknowledged_bytes < a.retained_bytes,
          )
      ) {
        const chunk = g.collector.readFailureChunk(g.c, id);
        g.collector.acknowledgeFailureChunk(g.c, {
          ...chunk,
          durable_receipt_id: `host-${id}-${chunk.offset}`,
        });
      }
    };
    release(first);
    expect(g.collector.failureState(g.c).held_bytes).toBe(0);
    expect(() =>
      (g.collector as any).hold(g.c, first, Buffer.from("reused")),
    ).toThrow("duplicate-private-artifact");
    for (let index = 0; index < 127; index++) {
      const id = `late-${index}`;
      (g.collector as any).hold(g.c, id, Buffer.from("x"));
      release(id);
    }
    expect(g.collector.failureState(g.c).held_bytes).toBe(0);
    (g.collector as any).hold(g.c, "late-over-cap", Buffer.from("y"));
    expect(g.collector.failureState(g.c).artifacts).toHaveLength(128);
    expect(
      g.collector
        .failureState(g.c)
        .artifacts.some((a) => a.artifact_id === "late-over-cap"),
    ).toBe(false);
    expect(g.collector.failureState(g.c).discarded_bytes).toBeGreaterThan(0);
  });
});

test("failure ownership never reuses cumulative byte allowance after an ACK", async () => {
  await withQualifiedGraph("switch", "late-capture", async (g) => {
    const work = g.start();
    work.catch(() => {});
    while (g.captureCount < 1) await g.pulse();
    g.abort.abort();
    await expect(work).rejects.toThrow("cancelled");
    g.resolveCapture();
    await g.pulse();
    const first = g.collector.failureState(g.c).artifacts[0].artifact_id;
    while (
      g.collector.failureState(g.c).artifacts[0].acknowledged_bytes <
      g.collector.failureState(g.c).artifacts[0].retained_bytes
    ) {
      const chunk = g.collector.readFailureChunk(g.c, first);
      g.collector.acknowledgeFailureChunk(g.c, {
        ...chunk,
        durable_receipt_id: `host-${chunk.offset}`,
      });
    }
    expect(g.collector.failureState(g.c).held_bytes).toBe(0);
    (g.collector as any).failureRetainedBytes = 128 * 1024 * 1024 - 1;
    (g.collector as any).hold(g.c, "last-byte", Buffer.from("xy"));
    const last = g.collector.readFailureChunk(g.c, "last-byte");
    expect(last.bytes).toBe(1);
    expect(g.collector.failureState(g.c).discarded_bytes).toBeGreaterThan(0);
    g.collector.acknowledgeFailureChunk(g.c, {
      ...last,
      durable_receipt_id: "host-last-byte",
    });
    expect(g.collector.failureState(g.c).held_bytes).toBe(0);
    (g.collector as any).hold(g.c, "over-byte-cap", Buffer.from("z"));
    expect(g.collector.failureState(g.c).artifacts).toHaveLength(2);
    expect(
      g.collector
        .failureState(g.c)
        .artifacts.some((a) => a.artifact_id === "over-byte-cap"),
    ).toBe(false);
    expect(g.collector.failureState(g.c).discarded_bytes).toBeGreaterThan(1);
  });
});

test("v2 failure snapshot keeps zero, partial and fully ACKed inventory", async () => {
  await withQualifiedGraph("switch", "late-capture", async (g) => {
    const work = g.start();
    work.catch(() => {});
    while (g.captureCount < 1) await g.pulse();
    g.abort.abort();
    await expect(work).rejects.toThrow("cancelled");
    g.resolveCapture();
    await g.pulse();
    const zero = g.collector.failureState(g.c);
    const zeroRaw = g.collector.failureSnapshotBytes(g.c);
    const zeroWire = JSON.parse(zeroRaw.toString("utf8"));
    expect(zeroRaw).toEqual(Buffer.from(JSON.stringify(zeroWire), "utf8"));
    expect(zeroWire).toMatchObject({
      ...zero,
      observed_ns: expect.any(String),
    });
    expect(zeroRaw.length).toBeLessThanOrEqual(LIMITS.failureSnapshot);
    for (const invalid of [
      { value: { ...zero, failure_ns: null }, code: "wire-string" },
      { value: { ...zero, retention_deadline_ns: null }, code: "wire-string" },
      { value: { ...zero, cause: null }, code: "wire-string" },
      {
        value: {
          ...zero,
          held_bytes: 0,
          artifacts: [
            { ...zero.artifacts[0], retained_bytes: 0, acknowledged_bytes: 0 },
          ],
        },
        code: "wire-minimum",
      },
    ]) {
      expect(() => validateWire("failure", invalid.value)).toThrow(
        invalid.code,
      );
      expect(() => encodeFailureSnapshot(invalid.value)).toThrow(invalid.code);
    }
    expect(() =>
      g.collector.failureSnapshotBytes({ ...g.c, sample_id: "foreign" }),
    ).toThrow("foreign-failure-snapshot");
    expect(() => encodeFailureSnapshot({ ...zero, cause: "\ud800" })).toThrow(
      "wire-unicode-scalar",
    );
    expect(() =>
      encodeFailureSnapshot({
        ...zero,
        cause: "x".repeat(LIMITS.failureSnapshot),
      }),
    ).toThrow();
    const id = zero.artifacts[0].artifact_id;
    expect(zero).toMatchObject({ wire_version: 2, held_bytes: g.png.length });
    expect(zero.artifacts[0]).toMatchObject({
      acknowledged_bytes: 0,
      retained_bytes: g.png.length,
      source_record_refs: [],
    });
    const first = g.collector.readFailureChunk(g.c, id);
    expect(first.source_record_ref).toEqual({ kind: "independent" });
    g.collector.acknowledgeFailureChunk(g.c, {
      ...first,
      durable_receipt_id: "host-first",
    });
    const partial = g.collector.failureState(g.c);
    const partialRaw = g.collector.failureSnapshotBytes(g.c);
    const partialWire = JSON.parse(partialRaw.toString("utf8"));
    expect(partialRaw).toEqual(
      Buffer.from(JSON.stringify(partialWire), "utf8"),
    );
    expect(partialWire).toMatchObject({
      ...partial,
      observed_ns: expect.any(String),
    });
    expect(partial.artifacts[0].acknowledged_bytes).toBe(first.bytes);
    expect(partial.held_bytes).toBe(g.png.length - first.bytes);
    while (g.collector.failureState(g.c).held_bytes) {
      const next = g.collector.readFailureChunk(g.c, id);
      g.collector.acknowledgeFailureChunk(g.c, {
        ...next,
        durable_receipt_id: `host-${next.offset}`,
      });
    }
    const full = g.collector.failureState(g.c);
    const fullRaw = g.collector.failureSnapshotBytes(g.c);
    const fullWire = JSON.parse(fullRaw.toString("utf8"));
    expect(fullRaw).toEqual(Buffer.from(JSON.stringify(fullWire), "utf8"));
    expect(fullWire).toMatchObject({
      ...full,
      observed_ns: expect.any(String),
    });
    expect(fullRaw.at(-1)).toBe("}".charCodeAt(0));
    expect(full.artifacts).toHaveLength(1);
    expect(full.artifacts[0]).toMatchObject({
      acknowledged_bytes: g.png.length,
      retained_bytes: g.png.length,
      sha256: digest(g.png),
    });
    expect(full.held_bytes).toBe(0);
    const missingRef = structuredClone(full);
    delete missingRef.artifacts[0].source_record_refs;
    expect(() => validateWire("failure", missingRef)).toThrow("wire-required");
    const forgedRef = structuredClone(full);
    forgedRef.artifacts[0].source_record_refs = [
      {
        kind: "native-record",
        sequence: 0,
        purpose: "rejected-capture",
        artifact_id: "foreign",
        chunk_index: 0,
        artifact_offset: 0,
        bytes: 1,
      },
    ];
    expect(() => validateWire("failure", forgedRef)).toThrow("wire-minimum");
    const overrunRef = structuredClone(full);
    overrunRef.artifacts[0].source_record_refs = [
      {
        ...forgedRef.artifacts[0].source_record_refs[0],
        sequence: 1,
        artifact_offset: g.png.length,
      },
    ];
    expect(() => validateWire("failure", overrunRef)).toThrow(
      "failure-v2-source-range",
    );
    const duplicate = structuredClone(full);
    duplicate.artifacts.push(structuredClone(duplicate.artifacts[0]));
    expect(() => validateWire("failure", duplicate)).toThrow(
      "failure-v2-duplicate-artifact",
    );
    const falseHeld = structuredClone(full);
    falseHeld.held_bytes = 1;
    expect(() => validateWire("failure", falseHeld)).toThrow(
      "failure-v2-cumulative-budget",
    );
  });
});

test("v2 source reference requires a real record ACK and exact callback echo", async () => {
  await withQualifiedGraph("switch", "late-capture", async (g) => {
    const raw = Buffer.from("record-owned");
    const id = "private-source";
    (g.collector as any).hold(g.c, id, raw);
    await (g.collector as any).send(
      g.c,
      async (line: Buffer) => JSON.parse(line.toString()).sequence,
      {
        kind: "private-chunk",
        artifact_id: "capture-source",
        purpose: "rejected-capture",
        chunk_index: 0,
        data: raw.toString("base64"),
      },
      (sequence: number) =>
        (g.collector as any).recordAckSource(
          id,
          {
            kind: "native-record",
            sequence,
            purpose: "rejected-capture",
            artifact_id: "capture-source",
            chunk_index: 0,
            artifact_offset: 0,
            bytes: raw.length,
          },
          raw,
        ),
    );
    (g.collector as any).failOwnership("cancelled");
    const state = g.collector.failureState(g.c);
    expect(state.artifacts[0].source_record_refs).toEqual([]);
    const chunk = g.collector.readFailureChunk(g.c, id);
    const ref = chunk.source_record_ref;
    expect(ref).toMatchObject({
      kind: "native-record",
      sequence: 1,
      artifact_id: "capture-source",
      bytes: raw.length,
    });
    expect(chunk.source_record_ref).toEqual(ref);
    for (const fake of [
      undefined,
      { ...ref, sequence: 2 },
      { ...ref, artifact_id: id },
      { kind: "independent" },
    ]) {
      expect(() =>
        g.collector.acknowledgeFailureChunk(g.c, {
          ...chunk,
          source_record_ref: fake,
          durable_receipt_id: "forged",
        }),
      ).toThrow("failure-retention-ack");
      expect(g.collector.failureState(g.c).held_bytes).toBe(raw.length);
    }
    g.collector.acknowledgeFailureChunk(g.c, {
      ...chunk,
      durable_receipt_id: "real-host",
    });
    expect(
      g.collector.failureState(g.c).artifacts[0].source_record_refs,
    ).toEqual([ref]);
  });
});

test("v2 partial failure snapshot exposes source refs only through durable callback ACK", async () => {
  await withQualifiedGraph("switch", "late-capture", async (g) => {
    const raw = Buffer.alloc(49153, 0x61);
    const id = "private-two-chunks";
    (g.collector as any).hold(g.c, id, raw);
    for (let index = 0; index < 2; index++) {
      const offset = index * 49152;
      const part = raw.subarray(offset, offset + 49152);
      await (g.collector as any).send(
        g.c,
        async (line: Buffer) => JSON.parse(line.toString()).sequence,
        {
          kind: "private-chunk",
          artifact_id: "capture-two-chunks",
          purpose: "rejected-capture",
          chunk_index: index,
          data: part.toString("base64"),
        },
        (sequence: number) =>
          (g.collector as any).recordAckSource(
            id,
            {
              kind: "native-record",
              sequence,
              purpose: "rejected-capture",
              artifact_id: "capture-two-chunks",
              chunk_index: index,
              artifact_offset: offset,
              bytes: part.length,
            },
            part,
          ),
      );
    }
    (g.collector as any).failOwnership("cancelled");
    expect(
      g.collector.failureState(g.c).artifacts[0].source_record_refs,
    ).toEqual([]);
    const first = g.collector.readFailureChunk(g.c, id);
    expect(first.source_record_ref).toMatchObject({ sequence: 1 });
    g.collector.acknowledgeFailureChunk(g.c, {
      ...first,
      durable_receipt_id: "host-first",
    });
    const partial = g.collector.failureState(g.c).artifacts[0];
    expect(partial.acknowledged_bytes).toBe(49152);
    expect(partial.source_record_refs).toEqual([first.source_record_ref]);
    const next = g.collector.readFailureChunk(g.c, id);
    expect(next.source_record_ref).toMatchObject({ sequence: 2 });
    g.collector.acknowledgeFailureChunk(g.c, {
      ...next,
      durable_receipt_id: "host-second",
    });
    const complete = g.collector.failureState(g.c).artifacts[0];
    expect(complete.acknowledged_bytes).toBe(raw.length);
    expect(complete.source_record_refs).toEqual([
      first.source_record_ref,
      next.source_record_ref,
    ]);
  });
});

test("v2 source reference stays independent after a refused record ACK", async () => {
  await withQualifiedGraph("switch", "late-capture", async (g) => {
    const raw = Buffer.from("unacknowledged");
    const id = "private-refused";
    (g.collector as any).hold(g.c, id, raw);
    await expect(
      (g.collector as any).send(
        g.c,
        async (line: Buffer) => JSON.parse(line.toString()).sequence + 1,
        {
          kind: "private-chunk",
          artifact_id: "capture-source",
          purpose: "rejected-capture",
          chunk_index: 0,
          data: raw.toString("base64"),
        },
        (sequence: number) =>
          (g.collector as any).recordAckSource(
            id,
            {
              kind: "native-record",
              sequence,
              purpose: "rejected-capture",
              artifact_id: "capture-source",
              chunk_index: 0,
              artifact_offset: 0,
              bytes: raw.length,
            },
            raw,
          ),
      ),
    ).rejects.toThrow("transport");
    (g.collector as any).failOwnership("transport");
    const state = g.collector.failureState(g.c);
    expect(state.artifacts[0].source_record_refs).toEqual([]);
    expect(g.collector.readFailureChunk(g.c, id).source_record_ref).toEqual({
      kind: "independent",
    });
  });
});

test("connected open control is one-shot and starts no measured resources before actual open", async () => {
  await withQualifiedGraph("live_visible", "manual-open", async (g) => {
    const work = g.start();
    work.catch(() => {});
    while (!g.rows.some((r) => r.observation.kind === "ready")) await g.pulse();
    await g.pulse();
    expect(
      g.rows.some(
        (r) =>
          r.observation.kind === "resource" &&
          r.observation.phase === "preopen",
      ),
    ).toBe(true);
    expect(g.rows.some((r) => r.observation.kind === "resource-boundary")).toBe(
      false,
    );
    g.control("open", g.now());
    expect(() => g.control("open", g.now())).toThrow(
      "foreign-or-duplicate-open",
    );
    await g.complete();
    await work;
    const open = g.rows.find((r) => r.observation.kind === "open").observation;
    const begin = g.rows.find(
      (r) =>
        r.observation.kind === "resource-boundary" &&
        r.observation.phase === "begin",
    ).observation;
    expect(BigInt(begin.observed_ns)).toBeGreaterThanOrEqual(
      BigInt(open.received_ns),
    );
  });
});

// Each mutation must fail at the actual public ACK boundary before any retained
// bytes or offset change, even when the artifact digest itself is correct.
const failureAckEnvelopeKeys = [
  "wire_version",
  "attempt_id",
  "protocol_id",
  "sample_id",
  "action_id",
  "context_id",
  "page_id",
  "window_id",
  "clock_id",
];
const failureAckMetadataKeys = [
  "observed_bytes",
  "retained_bytes",
  "retained_sha256",
  "artifact_received_ns",
  "failure_ns",
  "retention_deadline_ns",
  "source_record_ref",
];
for (const mutation of [
  ...[...failureAckEnvelopeKeys, ...failureAckMetadataKeys].flatMap((key) => [
    { key, mode: "missing" },
    { key, mode: "foreign" },
  ]),
  { key: "all", mode: "missing" },
]) {
  test(`connected failure ACK rejects ${mutation.mode} ${mutation.key} without releasing evidence`, async () => {
    await withQualifiedGraph("switch", "late-capture", async (g) => {
      const work = g.start();
      work.catch(() => {});
      while (g.captureCount < 1) await g.pulse();
      g.abort.abort();
      await expect(work).rejects.toThrow("cancelled");
      g.resolveCapture();
      await g.pulse();
      const before = g.collector.failureState(g.c);
      expect(before.disposition).toBe("partial");
      const id = before.artifacts[0].artifact_id;
      const chunk = g.collector.readFailureChunk(g.c, id);
      expect(chunk).toMatchObject({
        observed_bytes: before.artifacts[0].observed_bytes,
        retained_bytes: before.artifacts[0].retained_bytes,
        retained_sha256: before.artifacts[0].sha256,
        artifact_received_ns: before.artifacts[0].received_ns,
        failure_ns: before.failure_ns,
        retention_deadline_ns: before.retention_deadline_ns,
      });
      const receipt: Record<string, any> = {
        ...chunk,
        durable_receipt_id: "foreign-durable",
      };
      if (mutation.key === "all")
        for (const key of failureAckEnvelopeKeys) delete receipt[key];
      else if (mutation.mode === "missing") delete receipt[mutation.key];
      else
        receipt[mutation.key] = mutation.key === "wire_version" ? 99 : "OTHER";
      expect(() => g.collector.acknowledgeFailureChunk(g.c, receipt)).toThrow(
        "failure-retention-ack",
      );
      const after = g.collector.failureState(g.c);
      expect({ ...after, observed_ns: before.observed_ns }).toEqual(before);
      expect(g.collector.readFailureChunk(g.c, id)).toEqual(chunk);

      // A correct envelope still releases exactly each durable chunk; public
      // state remains partial until every byte of the acquired PNG is saved.
      const saved: Buffer[] = [];
      let released = 0;
      while (
        g.collector.failureState(g.c).artifacts[0].acknowledged_bytes <
        g.collector.failureState(g.c).artifacts[0].retained_bytes
      ) {
        const next = g.collector.readFailureChunk(g.c, id);
        expect(next.offset).toBe(released);
        expect(next.retained_bytes).toBe(g.png.length);
        expect(next.retained_sha256).toBe(digest(g.png));
        saved.push(Buffer.from(next.data));
        g.collector.acknowledgeFailureChunk(g.c, {
          ...next,
          durable_receipt_id: `durable-${next.offset}`,
        });
        released += next.bytes;
        const state = g.collector.failureState(g.c);
        expect(state.held_bytes).toBe(g.png.length - released);
        expect(state.disposition).toBe(
          released === g.png.length ? "settled" : "partial",
        );
        expect(state.artifacts[0].acknowledged_bytes).toBe(released);
      }
      expect(Buffer.concat(saved)).toEqual(g.png);
    });
  });
}
