/** Bounded exchange over caller-owned byte streams. The caller owns deadlines,
 * teardown, and the already registered one-command process association. */
import { LIMITS, requireFact } from "./native-contract";
import type { Exchange } from "./native-failed-handoff";

const MAGIC = Buffer.from("OC3B", "ascii");
const HEADER = 45;
const CHUNK = 64 * 1024;
const RECORD = 1;
const CALLBACK = 2;
const TERMINAL = 3;
const ACK = 4;
const MAX_ACK = 16 * 1024;

/** read must return at most maxBytes; write returns the number actually written.
 * An empty read means EOF. Neither operation may be silently retried by this layer. */
export interface NativeFailedByteStream {
  read(maxBytes: number): Promise<Buffer | null>;
  write(bytes: Buffer): Promise<number>;
}

function payloadCap(kind: number): number {
  switch (kind) {
    case RECORD:
      return LIMITS.line - 1;
    case CALLBACK:
      return LIMITS.line + LIMITS.chunk;
    case TERMINAL:
      return LIMITS.failureSnapshot;
    case ACK:
      return MAX_ACK;
    default:
      throw new Error("handoff-stream-kind");
  }
}

async function readExact(
  stream: NativeFailedByteStream,
  size: number,
  requireUnpoisoned: () => void,
): Promise<Buffer> {
  const result = Buffer.allocUnsafe(size);
  let position = 0;
  while (position < size) {
    requireUnpoisoned();
    const part = await stream.read(Math.min(CHUNK, size - position));
    requireUnpoisoned();
    requireFact(
      Buffer.isBuffer(part) &&
        part.length > 0 &&
        part.length <= Math.min(CHUNK, size - position),
      "handoff-stream-eof",
    );
    part.copy(result, position);
    position += part.length;
  }
  return result;
}

async function writeExact(
  stream: NativeFailedByteStream,
  bytes: Buffer,
  requireUnpoisoned: () => void,
): Promise<void> {
  for (let offset = 0; offset < bytes.length; offset += CHUNK) {
    requireUnpoisoned();
    const part = bytes.subarray(offset, Math.min(offset + CHUNK, bytes.length));
    const written = await stream.write(part);
    requireUnpoisoned();
    requireFact(written === part.length, "handoff-stream-short-write");
  }
}

/** The key is supplied by the caller's preregistration, never learned from a frame.
 * A pending read/write has no generic cancellation guarantee; the owning caller must
 * enforce its own deadline by tearing down the stream and process. */
export function createNativeFailedStreamExchange(
  stream: NativeFailedByteStream,
  commandSha256: string,
): Exchange {
  requireFact(/^[0-9a-f]{64}$/.test(commandSha256), "handoff-stream-key");
  const key = Buffer.from(commandSha256, "hex");
  let sequence = 0;
  let busy = false;
  let poisoned = false;
  let terminal = false;
  const requireUnpoisoned = () =>
    requireFact(!poisoned, "handoff-stream-closed");

  return async (request: Buffer): Promise<Buffer> => {
    if (busy) {
      poisoned = true;
      throw new Error("handoff-stream-overlap");
    }
    requireFact(!poisoned && !terminal, "handoff-stream-closed");
    busy = true;
    try {
      requireFact(
        Buffer.isBuffer(request) && request.length >= HEADER,
        "handoff-stream-request",
      );
      const kind = request.readUInt8(4);
      const size = request.readUInt32BE(41);
      requireFact(
        request.subarray(0, 4).equals(MAGIC) &&
          kind >= RECORD &&
          kind <= TERMINAL &&
          request.readUInt32BE(5) === sequence + 1 &&
          request.subarray(9, 41).equals(key) &&
          size > 0 &&
          size <= payloadCap(kind) &&
          request.length === HEADER + size,
        "handoff-stream-request",
      );
      await writeExact(stream, request, requireUnpoisoned);
      requireUnpoisoned();
      const header = await readExact(stream, HEADER, requireUnpoisoned);
      requireUnpoisoned();
      requireFact(
        header.subarray(0, 4).equals(MAGIC) &&
          header.readUInt8(4) === ACK &&
          header.readUInt32BE(5) === sequence + 1 &&
          header.subarray(9, 41).equals(key),
        "handoff-stream-ack-owner",
      );
      const replySize = header.readUInt32BE(41);
      requireFact(replySize <= payloadCap(ACK), "handoff-stream-ack-bound");
      const payload = await readExact(stream, replySize, requireUnpoisoned);
      requireUnpoisoned();
      sequence++;
      if (kind === TERMINAL) terminal = true;
      requireUnpoisoned();
      return Buffer.concat([header, payload]);
    } catch (error) {
      poisoned = true;
      throw error;
    } finally {
      busy = false;
    }
  };
}
