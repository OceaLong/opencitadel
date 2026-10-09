/** Structural validation is generated from Pydantic. Final semantic authority is
 * capacity_completion, never this Node transport or a closed status. */
import { createHash } from "node:crypto";
import { deflateSync, inflateSync } from "node:zlib";
import schema from "./native-schema.json";

export const LIMITS = Object.freeze({
  line: 96 * 1024,
  records: 65536,
  shard: 32 * 1024 * 1024,
  image: 8 * 1024 * 1024,
  chunk: 48 * 1024,
  capturesPerContext: 121,
  contexts: 10,
  width: 1440,
  height: 900,
  pending: 1,
  failureSnapshot: 8 * 1024 * 1024,
});
export type Obj = Record<string, any>;
export type NativeCommand = Obj & { intent: Obj; deadline_ns: string };
export type FailureSourceRecordRef = Readonly<{
  kind: "native-record";
  sequence: number;
  purpose: "rejected-capture" | "native-trace";
  artifact_id: string;
  chunk_index: number;
  artifact_offset: number;
  bytes: number;
}>;
export type FailureSourceRef =
  | FailureSourceRecordRef
  | Readonly<{ kind: "independent" }>;
export const digest = (value: string | Buffer) =>
  createHash("sha256").update(value).digest("hex");
export function requireFact(
  condition: unknown,
  code: string,
): asserts condition {
  if (!condition) throw new Error(code);
}

function requireUnicodeScalars(value: unknown): void {
  const pending: unknown[] = [value];
  const seen = new WeakSet<object>();
  while (pending.length) {
    const item = pending.pop();
    if (typeof item === "string") {
      for (let index = 0; index < item.length; index++) {
        const unit = item.charCodeAt(index);
        if (unit >= 0xd800 && unit <= 0xdbff) {
          requireFact(
            index + 1 < item.length &&
              item.charCodeAt(index + 1) >= 0xdc00 &&
              item.charCodeAt(index + 1) <= 0xdfff,
            "wire-unicode-scalar",
          );
          index++;
        } else {
          requireFact(unit < 0xdc00 || unit > 0xdfff, "wire-unicode-scalar");
        }
      }
    } else if (item !== null && typeof item === "object") {
      if (seen.has(item)) continue;
      seen.add(item);
      if (Array.isArray(item)) for (const nested of item) pending.push(nested);
      else
        for (const [key, nested] of Object.entries(item))
          pending.push(key, nested);
    }
  }
}

const idSchema = { type: "string", minLength: 1, maxLength: 255 };
const failureSourceRefSchema = {
  oneOf: [
    {
      type: "object",
      additionalProperties: false,
      properties: { kind: { const: "independent" } },
      required: ["kind"],
    },
    {
      type: "object",
      additionalProperties: false,
      properties: {
        kind: { const: "native-record" },
        sequence: { type: "integer", minimum: 1, maximum: LIMITS.records },
        purpose: { enum: ["rejected-capture", "native-trace"] },
        artifact_id: idSchema,
        chunk_index: { type: "integer", minimum: 0 },
        artifact_offset: {
          type: "integer",
          minimum: 0,
          maximum: 128 * 1024 * 1024,
        },
        bytes: { type: "integer", minimum: 1, maximum: LIMITS.chunk },
      },
      required: [
        "kind",
        "sequence",
        "purpose",
        "artifact_id",
        "chunk_index",
        "artifact_offset",
        "bytes",
      ],
    },
  ],
};
const oldFailureSchema: Obj = schema.failure;
const failureV2Schema: Obj = {
  ...oldFailureSchema,
  properties: {
    ...oldFailureSchema.properties,
    wire_version: { type: "integer", const: 2 },
    failure_ns: oldFailureSchema.properties.observed_ns,
    retention_deadline_ns: oldFailureSchema.properties.observed_ns,
    cause: idSchema,
    artifacts: {
      ...oldFailureSchema.properties.artifacts,
      items: {
        ...oldFailureSchema.$defs.NativeRetainedArtifact,
        properties: {
          ...oldFailureSchema.$defs.NativeRetainedArtifact.properties,
          retained_bytes: {
            ...oldFailureSchema.$defs.NativeRetainedArtifact.properties
              .retained_bytes,
            minimum: 1,
          },
          source_record_refs: {
            type: "array",
            maxItems: 4096,
            items: failureSourceRefSchema.oneOf[1],
          },
        },
        required: [
          ...oldFailureSchema.$defs.NativeRetainedArtifact.required,
          "source_record_refs",
        ],
      },
    },
  },
};

// Closed subset of the generated JSON Schema vocabulary; unknown keywords fail.
// This is only the transport shape gate; Python model validators remain required.
export function validateWire(
  kind: "command" | "record" | "control" | "failure",
  value: unknown,
): void {
  requireUnicodeScalars(value);
  const root: Obj =
    kind === "failure" && (value as Obj)?.wire_version === 2
      ? failureV2Schema
      : schema[kind];
  function check(s: Obj, v: any): void {
    const supported = new Set([
      "$defs",
      "$ref",
      "title",
      "description",
      "default",
      "discriminator",
      "type",
      "properties",
      "required",
      "additionalProperties",
      "items",
      "minItems",
      "maxItems",
      "minLength",
      "maxLength",
      "pattern",
      "minimum",
      "maximum",
      "exclusiveMinimum",
      "const",
      "enum",
      "anyOf",
      "oneOf",
    ]);
    requireFact(
      Object.keys(s).every((k) => supported.has(k)),
      "unsupported-schema-keyword",
    );
    if (s.$ref) return check(root.$defs[s.$ref.split("/").at(-1)], v);
    if (s.discriminator) {
      const ref = s.discriminator.mapping?.[v?.[s.discriminator.propertyName]];
      requireFact(typeof ref === "string", "wire-discriminator");
      return check({ $ref: ref }, v);
    }
    if (s.anyOf || s.oneOf) {
      const matches = (s.anyOf ?? s.oneOf).filter((x: Obj) => {
        try {
          check(x, v);
          return true;
        } catch {
          return false;
        }
      }).length;
      requireFact(s.oneOf ? matches === 1 : matches > 0, "wire-union");
      return;
    }
    if ("const" in s) requireFact(v === s.const, "wire-constant");
    if (s.enum) requireFact(s.enum.includes(v), "wire-enum");
    if (s.type === "null") requireFact(v === null, "wire-null");
    if (s.type === "boolean")
      requireFact(typeof v === "boolean", "wire-boolean");
    if (s.type === "integer" || s.type === "number") {
      requireFact(
        typeof v === "number" &&
          Number.isFinite(v) &&
          (s.type !== "integer" || Number.isSafeInteger(v)),
        "wire-number",
      );
      if (s.minimum !== undefined) requireFact(v >= s.minimum, "wire-minimum");
      if (s.maximum !== undefined) requireFact(v <= s.maximum, "wire-maximum");
      if (s.exclusiveMinimum !== undefined)
        requireFact(v > s.exclusiveMinimum, "wire-exclusive-minimum");
    }
    if (s.type === "string") {
      requireFact(typeof v === "string", "wire-string");
      if (s.minLength !== undefined)
        requireFact([...v].length >= s.minLength, "wire-string-min");
      if (s.maxLength !== undefined)
        requireFact([...v].length <= s.maxLength, "wire-string-max");
      if (s.pattern)
        requireFact(new RegExp(s.pattern, "u").test(v), "wire-pattern");
    }
    if (s.type === "array") {
      requireFact(Array.isArray(v), "wire-array");
      if (s.minItems !== undefined)
        requireFact(v.length >= s.minItems, "wire-array-min");
      if (s.maxItems !== undefined)
        requireFact(v.length <= s.maxItems, "wire-array-max");
      v.forEach((x: any) => check(s.items, x));
    }
    if (s.type === "object") {
      requireFact(
        v !== null && typeof v === "object" && !Array.isArray(v),
        "wire-object",
      );
      requireFact(
        (s.required ?? []).every((k: string) => Object.hasOwn(v, k)),
        "wire-required",
      );
      requireFact(
        s.additionalProperties !== false ||
          Object.keys(v).every((k) => Object.hasOwn(s.properties, k)),
        "wire-extra-key",
      );
      for (const [k, x] of Object.entries(v))
        if (s.properties[k]) check(s.properties[k], x);
    }
  }
  check(root, value);
  if (kind === "failure" && (value as Obj).wire_version === 2) {
    const snapshot = value as Obj;
    const artifacts = snapshot.artifacts as Obj[];
    const identities = new Set<string>();
    let retained = 0,
      held = 0,
      refs = 0;
    for (const artifact of artifacts) {
      requireFact(
        !identities.has(artifact.artifact_id),
        "failure-v2-duplicate-artifact",
      );
      identities.add(artifact.artifact_id);
      requireFact(
        artifact.observed_bytes >= artifact.retained_bytes &&
          artifact.acknowledged_bytes <= artifact.retained_bytes,
        "failure-v2-artifact-totals",
      );
      retained += artifact.retained_bytes;
      held += artifact.retained_bytes - artifact.acknowledged_bytes;
      let priorEnd = 0;
      for (const ref of artifact.source_record_refs as FailureSourceRecordRef[]) {
        requireFact(
          ref.artifact_offset >= priorEnd &&
            ref.artifact_offset + ref.bytes <= artifact.acknowledged_bytes,
          "failure-v2-source-range",
        );
        priorEnd = ref.artifact_offset + ref.bytes;
        refs++;
      }
    }
    requireFact(
      retained <= 128 * 1024 * 1024 &&
        refs <= 4096 &&
        held === snapshot.held_bytes,
      "failure-v2-cumulative-budget",
    );
  }
}

/** Exact producer bytes for a host-side failure.json handoff. The host must
 * independently reconcile these bytes against its durable callback prefix. */
export function encodeFailureSnapshot(snapshot: unknown): Buffer {
  validateWire("failure", snapshot);
  requireFact(
    (snapshot as Obj).wire_version === 2,
    "failure-snapshot-wire-version",
  );
  const raw = Buffer.from(JSON.stringify(snapshot), "utf8");
  requireFact(
    raw.length > 0 && raw.length <= LIMITS.failureSnapshot,
    "failure-snapshot-byte-bound",
  );
  return raw;
}
const identityKeys = [
  "attempt_id",
  "protocol_id",
  "sample_id",
  "action_id",
  "context_id",
  "page_id",
  "window_id",
  "clock_id",
];
export function envelope(command: NativeCommand) {
  return {
    wire_version: 1,
    ...Object.fromEntries(identityKeys.map((k) => [k, command[k]])),
  };
}
export class NativeLines {
  private partial = Buffer.alloc(0);
  private sequence = 0;
  private bytes = 0;
  private closed = false;
  private failed = false;
  constructor(private command: NativeCommand) {
    validateWire("command", command);
  }
  push(chunk: Buffer): Obj[] {
    requireFact(!this.failed, "transport-failed");
    try {
      return this.consume(chunk);
    } catch (error) {
      this.failed = true;
      throw error;
    }
  }
  private consume(chunk: Buffer): Obj[] {
    requireFact(
      !this.closed && chunk.length <= LIMITS.line,
      "transport-closed-or-chunk-overflow",
    );
    this.bytes += chunk.length;
    requireFact(this.bytes <= LIMITS.shard, "transport-shard-overflow");
    const input = Buffer.concat([this.partial, chunk]);
    const rows: Obj[] = [];
    let offset = 0;
    for (
      let end = input.indexOf(10);
      end >= 0;
      end = input.indexOf(10, offset)
    ) {
      const line = input.subarray(offset, end);
      offset = end + 1;
      requireFact(
        line.length > 0 && line.length < LIMITS.line,
        "transport-line-bound",
      );
      const string = new TextDecoder("utf-8", { fatal: true }).decode(line);
      // JSON.parse accepts duplicate keys. The canonical form comparison rejects
      // duplicates, whitespace variants, alternate number spelling and CRLF.
      const row = JSON.parse(string);
      requireFact(JSON.stringify(row) === string, "noncanonical-json");
      validateWire("record", row);
      requireFact(
        row.sequence === ++this.sequence && this.sequence <= LIMITS.records,
        "transport-sequence",
      );
      requireFact(
        identityKeys.every((k) => row[k] === this.command[k]),
        "transport-owner",
      );
      requireFact(!this.closed, "record-after-close");
      this.closed = row.observation.kind === "closed";
      rows.push(row);
    }
    this.partial = input.subarray(offset);
    requireFact(
      this.partial.length < LIMITS.line,
      "transport-partial-overflow",
    );
    return rows;
  }
  nextShard() {
    requireFact(
      !this.failed && this.partial.length === 0 && !this.closed,
      "shard-boundary",
    );
    this.bytes = 0;
  }
  end() {
    requireFact(
      !this.failed && this.partial.length === 0 && this.closed,
      "transport-partial-or-unclosed-eof",
    );
  }
}

export class FirstResponse {
  private bound = false;
  constructor(
    private command: NativeCommand,
    private trigger: bigint,
  ) {}
  accept(row: Obj): Obj {
    requireFact(!this.bound, "multiple-first-responses");
    this.bound = true; // a rejected first response consumes the slot too
    const i = this.command.intent;
    requireFact(
      row.action_id === this.command.action_id &&
        row.request_ns >= this.trigger &&
        row.received_ns >= row.request_ns,
      "old-or-foreign-action",
    );
    requireFact(
      row.scope_id === i.scope_id &&
        row.run_id === i.run_id &&
        row.step_id === i.step_id,
      "foreign-public-target",
    );
    requireFact(
      typeof row.public_id === "string" &&
        row.public_id.length > 0 &&
        typeof row.revision === "string",
      "missing-public-target",
    );
    return {
      sample_id: this.command.sample_id,
      action_id: this.command.action_id,
      page_id: this.command.page_id,
      context_id: this.command.context_id,
      clock_id: this.command.clock_id,
      request_id: row.request_id,
      response_id: row.response_id,
      request_ns: row.request_ns.toString(),
      received_ns: row.received_ns.toString(),
      target: {
        scope_id: row.scope_id,
        run_id: row.run_id,
        step_id: row.step_id,
        public_id: row.public_id,
        revision: row.revision,
      },
      session_id: i.session_id,
      batch_id: i.batch_id,
    };
  }
}
export type QualifiedBuild = {
  product: string;
  revision: string;
  executable_sha256: string;
  platform: "linux";
  source_qualification_digest: string;
};
// A new entry is a reviewed source change AFTER actual reference qualification.
// No current executable has been qualified; launch arguments never bypass this.
export const REVIEWED_BUILDS: readonly QualifiedBuild[] = Object.freeze([]);
export function qualifyBuild(
  facts: Obj,
  registry: readonly QualifiedBuild[],
): QualifiedBuild | null {
  const enabled = facts.argv?.some(
    (a: string) =>
      a.startsWith("--enable-features=") &&
      a.slice(18).split(",").includes("CDPScreenshotNewSurface"),
  );
  const conflict = facts.argv?.some(
    (a: string) =>
      a.startsWith("--disable-features=") &&
      a
        .slice(19)
        .split(",")
        .some((x) => x.split("<")[0] === "CDPScreenshotNewSurface"),
  );
  if (!enabled || conflict) return null;
  const matches = registry.filter(
    (r) =>
      ["product", "revision", "executable_sha256", "platform"].every(
        (k) => r[k as keyof QualifiedBuild] === facts[k],
      ) && /^[a-f0-9]{64}$/.test(r.source_qualification_digest),
  );
  return matches.length === 1 ? matches[0] : null;
}
function crc32(bytes: Buffer) {
  let c = 0xffffffff;
  for (const b of bytes) {
    c ^= b;
    for (let i = 0; i < 8; i++) c = (c >>> 1) ^ (c & 1 ? 0xedb88320 : 0);
  }
  return (c ^ 0xffffffff) >>> 0;
}
export function validatePNG(bytes: Buffer): {
  pixels: Buffer;
  channels: number;
} {
  requireFact(
    bytes.length > 32 &&
      bytes.length <= LIMITS.image &&
      bytes.subarray(0, 8).equals(Buffer.from("89504e470d0a1a0a", "hex")),
    "png-size-signature",
  );
  let pos = 8,
    header = false,
    end = false,
    channels = 0;
  const data: Buffer[] = [];
  while (pos < bytes.length) {
    requireFact(pos + 12 <= bytes.length, "png-truncated");
    const length = bytes.readUInt32BE(pos);
    const type = bytes.toString("ascii", pos + 4, pos + 8);
    requireFact(
      length <= LIMITS.image && pos + 12 + length <= bytes.length,
      "png-chunk-bound",
    );
    const body = bytes.subarray(pos + 8, pos + 8 + length);
    requireFact(
      crc32(bytes.subarray(pos + 4, pos + 8 + length)) ===
        bytes.readUInt32BE(pos + 8 + length),
      "png-crc",
    );
    if (!header) {
      requireFact(
        type === "IHDR" &&
          length === 13 &&
          body.readUInt32BE(0) === 1440 &&
          body.readUInt32BE(4) === 900 &&
          body[8] === 8 &&
          [2, 6].includes(body[9]) &&
          body[10] === 0 &&
          body[11] === 0 &&
          body[12] === 0,
        "png-geometry-format",
      );
      channels = body[9] === 6 ? 4 : 3;
      header = true;
    } else if (type === "IDAT") data.push(body);
    else if (type === "IEND") {
      requireFact(length === 0 && pos + 12 === bytes.length, "png-trailing");
      end = true;
    } else requireFact((type.charCodeAt(0) & 32) !== 0, "png-unknown-critical");
    pos += length + 12;
  }
  requireFact(end && data.length > 0, "png-incomplete");
  const stride = 1440 * channels + 1;
  const raw = inflateSync(Buffer.concat(data), {
    maxOutputLength: stride * 900,
  });
  requireFact(raw.length === stride * 900, "png-decoded-size");
  const pixels = Buffer.alloc(1440 * 900 * channels);
  const paeth = (a: number, b: number, c: number) => {
    const p = a + b - c,
      pa = Math.abs(p - a),
      pb = Math.abs(p - b),
      pc = Math.abs(p - c);
    return pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
  };
  for (let y = 0; y < 900; y++) {
    const filter = raw[y * stride];
    requireFact(filter <= 4, "png-filter");
    for (let x = 0; x < stride - 1; x++) {
      const at = y * (stride - 1) + x,
        left = x >= channels ? pixels[at - channels] : 0,
        up = y > 0 ? pixels[at - (stride - 1)] : 0,
        corner =
          y > 0 && x >= channels ? pixels[at - (stride - 1) - channels] : 0;
      pixels[at] =
        (raw[y * stride + 1 + x] +
          (filter === 0
            ? 0
            : filter === 1
              ? left
              : filter === 2
                ? up
                : filter === 3
                  ? Math.floor((left + up) / 2)
                  : paeth(left, up, corner))) &
        255;
    }
  }
  return { pixels, channels };
}

/** Version 1: outward integer hull of native + full DOM text range rectangles,
 * plus exactly 4 device pixels; no clamp, scale, color conversion or resample. */
export function cropProgressPNG(
  original: Buffer,
  rects: number[][],
): { bytes: Buffer; rect: number[]; channels: number } {
  requireFact(rects.length > 0 && rects.length <= 32, "crop-geometry");
  requireFact(
    rects.every(
      (r) => r.length === 4 && r.every(Number.isFinite) && r[2] > 0 && r[3] > 0,
    ),
    "crop-geometry",
  );
  const x = Math.floor(Math.min(...rects.map((r) => r[0]))) - 4,
    y = Math.floor(Math.min(...rects.map((r) => r[1]))) - 4;
  const right = Math.ceil(Math.max(...rects.map((r) => r[0] + r[2]))) + 4,
    bottom = Math.ceil(Math.max(...rects.map((r) => r[1] + r[3]))) + 4;
  const width = right - x,
    height = bottom - y;
  requireFact(
    x >= 0 &&
      y >= 0 &&
      right <= 1440 &&
      bottom <= 900 &&
      width <= 320 &&
      height <= 64,
    "crop-geometry",
  );
  const { pixels, channels } = validatePNG(original);
  const raw = Buffer.alloc((width * channels + 1) * height);
  for (let row = 0; row < height; row++)
    pixels.copy(
      raw,
      row * (width * channels + 1) + 1,
      ((y + row) * 1440 + x) * channels,
      ((y + row) * 1440 + right) * channels,
    );
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = 8;
  header[9] = channels === 4 ? 6 : 2;
  const chunk = (name: string, body: Buffer) => {
    const result = Buffer.alloc(body.length + 12);
    result.writeUInt32BE(body.length, 0);
    result.write(name, 4);
    body.copy(result, 8);
    result.writeUInt32BE(
      crc32(result.subarray(4, 8 + body.length)),
      8 + body.length,
    );
    return result;
  };
  const bytes = Buffer.concat([
    Buffer.from("89504e470d0a1a0a", "hex"),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(raw)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
  requireFact(bytes.length <= 96 * 1024, "crop-encoded-bound");
  return { bytes, rect: [x, y, width, height], channels };
}
