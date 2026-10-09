/** In-memory, one-command failed-evidence transport. The launcher owns streams,
 * process lifetime and preregistered origin; this layer sends no paths. */
import { createHash } from "node:crypto";
import {
  LIMITS,
  type NativeCommand,
  requireFact,
  validateWire,
} from "./native-contract";
import { type Emit, NativeCollector } from "./native-collector";

const MAGIC = Buffer.from("OC3B", "ascii");
const HEADER = 45;
const RECORD = 1;
const CALLBACK = 2;
const TERMINAL = 3;
const ACK = 4;
const MAX_CALLBACK = LIMITS.line + LIMITS.chunk;
const MAX_ACK = 16 * 1024;

export type Exchange = (frame: Buffer) => Promise<Buffer>;

function commandKey(key: string): Buffer {
  requireFact(/^[0-9a-f]{64}$/.test(key), "handoff-command-key");
  return Buffer.from(key, "hex");
}

function frame(
  kind: number,
  sequence: number,
  key: Buffer,
  payload: Buffer,
): Buffer {
  requireFact(
    Number.isSafeInteger(sequence) &&
      sequence > 0 &&
      sequence <= 0xffffffff &&
      payload.length <= LIMITS.failureSnapshot,
    "handoff-frame-bound",
  );
  const header = Buffer.alloc(HEADER);
  MAGIC.copy(header);
  header.writeUInt8(kind, 4);
  header.writeUInt32BE(sequence, 5);
  key.copy(header, 9);
  header.writeUInt32BE(payload.length, 41);
  return Buffer.concat([header, payload]);
}

function ackPayload(raw: Buffer, sequence: number, key: Buffer): Buffer {
  requireFact(
    Buffer.isBuffer(raw) && raw.length >= HEADER,
    "handoff-ack-truncated",
  );
  requireFact(
    raw.subarray(0, 4).equals(MAGIC) &&
      raw.readUInt8(4) === ACK &&
      raw.readUInt32BE(5) === sequence &&
      raw.subarray(9, 41).equals(key),
    "handoff-ack-owner",
  );
  const length = raw.readUInt32BE(41);
  requireFact(
    length <= MAX_ACK && raw.length === HEADER + length,
    "handoff-ack-length",
  );
  return raw.subarray(HEADER);
}

/** A missing ACK poisons this session. Never retry an uncertain host append. */
export class NativeFailedHandoffClient {
  private readonly key: Buffer;
  private sequence = 0;
  private busy = false;
  private poisoned = false;
  private terminal = false;

  constructor(
    private readonly command: NativeCommand,
    commandSha256: string,
    private readonly exchange: Exchange,
  ) {
    validateWire("command", command);
    this.key = commandKey(commandSha256);
  }

  private async send(kind: number, payload: Buffer): Promise<Buffer> {
    requireFact(
      !this.poisoned && !this.terminal && !this.busy,
      "handoff-closed",
    );
    this.busy = true;
    try {
      const sequence = this.sequence + 1;
      const reply = await this.exchange(
        frame(kind, sequence, this.key, payload),
      );
      const acknowledged = ackPayload(reply, sequence, this.key);
      this.sequence = sequence;
      return acknowledged;
    } catch (error) {
      this.poisoned = true;
      throw error;
    } finally {
      this.busy = false;
    }
  }

  readonly emit: Emit = async (line) => {
    requireFact(
      Buffer.isBuffer(line) &&
        line.length > 1 &&
        line.length < LIMITS.line &&
        line.at(-1) === 10,
      "handoff-record-bound",
    );
    try {
      const receipt = await this.send(RECORD, Buffer.from(line));
      requireFact(receipt.length === 4, "handoff-record-ack");
      return receipt.readUInt32BE(0);
    } catch (error) {
      this.poisoned = true;
      throw error;
    }
  };

  readonly emitFailureChunk = async (
    chunk: ReturnType<NativeCollector["readFailureChunk"]>,
  ) => {
    requireFact(Buffer.isBuffer(chunk.data), "handoff-callback-data");
    const { data, ...metadata } = chunk;
    const raw = Buffer.from(JSON.stringify(metadata), "utf8");
    requireFact(
      raw.length > 0 &&
        raw.length < LIMITS.line &&
        data.length > 0 &&
        data.length <= LIMITS.chunk &&
        raw.length + data.length + 4 <= MAX_CALLBACK,
      "handoff-callback-bound",
    );
    const length = Buffer.alloc(4);
    length.writeUInt32BE(raw.length);
    try {
      const receipt = await this.send(
        CALLBACK,
        Buffer.concat([length, raw, Buffer.from(data)]),
      );
      const text = receipt.toString("utf8");
      const parsed = JSON.parse(text);
      requireFact(
        parsed !== null &&
          typeof parsed === "object" &&
          !Array.isArray(parsed) &&
          Buffer.from(JSON.stringify(parsed)).equals(receipt),
        "handoff-callback-ack",
      );
      return parsed;
    } catch (error) {
      this.poisoned = true;
      throw error;
    }
  };

  /** Calling the producer's terminal barrier is mandatory; ordinary diagnostic
   * failureSnapshotBytes must never be sent as a close candidate. */
  async terminalFromCollector(collector: NativeCollector): Promise<Buffer> {
    requireFact(
      !this.poisoned && !this.terminal && !this.busy,
      "handoff-closed",
    );
    const bytes = collector.terminalFailureSnapshotBytes(this.command);
    try {
      const receipt = await this.send(TERMINAL, bytes);
      requireFact(
        receipt.length === 32 &&
          receipt.equals(createHash("sha256").update(bytes).digest()),
        "handoff-terminal-ack",
      );
      this.terminal = true;
      return Buffer.from(bytes);
    } catch (error) {
      this.poisoned = true;
      throw error;
    }
  }
}
