import { test, expect } from "@playwright/test";
import fixture from "../performance/native-wire-fixture-v1.json";
import { NativeFailedHandoffClient } from "../performance/native-failed-handoff";
import {
  createNativeFailedStreamExchange,
  type NativeFailedByteStream,
} from "../performance/native-failed-stream";

const key = "00".repeat(32);

function reply(sequence: number, payload: Buffer, owner = key): Buffer {
  const header = Buffer.alloc(45);
  header.write("OC3B", 0, "ascii");
  header.writeUInt8(4, 4);
  header.writeUInt32BE(sequence, 5);
  Buffer.from(owner, "hex").copy(header, 9);
  header.writeUInt32BE(payload.length, 41);
  return Buffer.concat([header, payload]);
}

function request(kind: number, payload: Buffer): Buffer {
  const header = Buffer.alloc(45);
  header.write("OC3B", 0, "ascii");
  header.writeUInt8(kind, 4);
  header.writeUInt32BE(1, 5);
  Buffer.from(key, "hex").copy(header, 9);
  header.writeUInt32BE(payload.length, 41);
  return Buffer.concat([header, payload]);
}

class MemoryStream implements NativeFailedByteStream {
  readonly writes: Buffer[] = [];
  readonly readSizes: number[] = [];
  consumedBytes = 0;
  private incoming: Buffer;

  constructor(
    incoming: Buffer,
    private readonly fragment = 7,
  ) {
    this.incoming = incoming;
  }

  async read(maxBytes: number): Promise<Buffer | null> {
    this.readSizes.push(maxBytes);
    if (this.incoming.length === 0) return null;
    const count = Math.min(maxBytes, this.fragment, this.incoming.length);
    const part = this.incoming.subarray(0, count);
    this.incoming = this.incoming.subarray(count);
    this.consumedBytes += count;
    return part;
  }

  async write(bytes: Buffer): Promise<number> {
    this.writes.push(Buffer.from(bytes));
    return bytes.length;
  }
}

test("fragmented ACKs and coalesced next ACK preserve one-command order", async () => {
  const stream = new MemoryStream(
    Buffer.concat([
      reply(1, Buffer.from([0, 0, 0, 1])),
      reply(2, Buffer.from([0, 0, 0, 2])),
    ]),
    3,
  );
  const client = new NativeFailedHandoffClient(
    fixture.command,
    key,
    createNativeFailedStreamExchange(stream, key),
  );
  expect(await client.emit(Buffer.from("abc\n"))).toBe(1);
  expect(await client.emit(Buffer.from("def\n"))).toBe(2);
  const outbound = Buffer.concat(stream.writes);
  expect(outbound.subarray(0, 45).readUInt32BE(5)).toBe(1);
  expect(outbound.subarray(49, 94).readUInt32BE(5)).toBe(2);
  expect(stream.readSizes.every((size) => size <= 64 * 1024)).toBe(true);
  expect(stream.writes.every((part) => part.length <= 64 * 1024)).toBe(true);
});

test("declared oversized ACK is refused after its header without payload read", async () => {
  const oversized = reply(1, Buffer.alloc(0));
  oversized.writeUInt32BE(16 * 1024 + 1, 41);
  const stream = new MemoryStream(oversized);
  const client = new NativeFailedHandoffClient(
    fixture.command,
    key,
    createNativeFailedStreamExchange(stream, key),
  );
  await expect(client.emit(Buffer.from("abc\n"))).rejects.toThrow(
    "handoff-stream-ack-bound",
  );
  expect(stream.consumedBytes).toBe(45);
  await expect(client.emit(Buffer.from("abc\n"))).rejects.toThrow(
    "handoff-closed",
  );
});

test("wrong owner, sequence, truncated payload, and lost ACK close the exchange", async () => {
  for (const response of [
    reply(1, Buffer.alloc(4), "11".repeat(32)),
    reply(2, Buffer.alloc(4)),
    reply(1, Buffer.alloc(3)),
    Buffer.alloc(12),
  ]) {
    const stream = new MemoryStream(response);
    const client = new NativeFailedHandoffClient(
      fixture.command,
      key,
      createNativeFailedStreamExchange(stream, key),
    );
    await expect(client.emit(Buffer.from("abc\n"))).rejects.toThrow();
    const count = stream.writes.length;
    await expect(client.emit(Buffer.from("abc\n"))).rejects.toThrow(
      "handoff-closed",
    );
    expect(stream.writes).toHaveLength(count);
  }
});

test("uncertain short write is never resumed or retried", async () => {
  const writes: Buffer[] = [];
  const stream: NativeFailedByteStream = {
    async read() {
      throw new Error("must not read after short write");
    },
    async write(bytes) {
      writes.push(Buffer.from(bytes));
      return bytes.length - 1;
    },
  };
  const client = new NativeFailedHandoffClient(
    fixture.command,
    key,
    createNativeFailedStreamExchange(stream, key),
  );
  await expect(client.emit(Buffer.from("abc\n"))).rejects.toThrow(
    "handoff-stream-short-write",
  );
  await expect(client.emit(Buffer.from("abc\n"))).rejects.toThrow(
    "handoff-closed",
  );
  expect(writes).toHaveLength(1);
});

test("large request is chunked and a short later chunk is never replayed", async () => {
  const payload = Buffer.alloc(150 * 1024, 7);
  const request = Buffer.alloc(45 + payload.length);
  request.write("OC3B", 0, "ascii");
  request.writeUInt8(3, 4);
  request.writeUInt32BE(1, 5);
  Buffer.from(key, "hex").copy(request, 9);
  request.writeUInt32BE(payload.length, 41);
  payload.copy(request, 45);
  const written: Buffer[] = [];
  const stream: NativeFailedByteStream = {
    async read() {
      throw new Error("ACK must not be read after a short write");
    },
    async write(bytes) {
      written.push(Buffer.from(bytes));
      return written.length === 2 ? bytes.length - 1 : bytes.length;
    },
  };
  const exchange = createNativeFailedStreamExchange(stream, key);
  await expect(exchange(request)).rejects.toThrow("handoff-stream-short-write");
  expect(written.map((part) => part.length)).toEqual([64 * 1024, 64 * 1024]);
  await expect(exchange(request)).rejects.toThrow("handoff-stream-closed");
  expect(written).toHaveLength(2);
});

test("overlap during a delayed valid ACK poisons the pending exchange", async () => {
  let markReadStarted!: () => void;
  const readStarted = new Promise<void>((resolve) => {
    markReadStarted = resolve;
  });
  let releaseAck!: () => void;
  const heldAck = new Promise<void>((resolve) => {
    releaseAck = resolve;
  });
  let incoming = reply(1, Buffer.alloc(4));
  let firstRead = true;
  const writes: Buffer[] = [];
  const stream: NativeFailedByteStream = {
    async read(maxBytes) {
      if (firstRead) {
        firstRead = false;
        markReadStarted();
        await heldAck;
      }
      if (incoming.length === 0) return null;
      const part = incoming.subarray(0, maxBytes);
      incoming = incoming.subarray(part.length);
      return part;
    },
    async write(bytes) {
      writes.push(Buffer.from(bytes));
      return bytes.length;
    },
  };
  const exchange = createNativeFailedStreamExchange(stream, key);
  const frame = request(1, Buffer.from("abc\n"));
  const pending = exchange(frame);
  await readStarted;
  await expect(exchange(frame)).rejects.toThrow("handoff-stream-overlap");
  releaseAck();
  await expect(pending).rejects.toThrow("handoff-stream-closed");
  await expect(exchange(frame)).rejects.toThrow("handoff-stream-closed");
  expect(writes).toHaveLength(1);
});

test("overlap during a chunk write prevents the next chunk", async () => {
  let markWriteStarted!: () => void;
  const writeStarted = new Promise<void>((resolve) => {
    markWriteStarted = resolve;
  });
  let releaseWrite!: (value: number) => void;
  const heldWrite = new Promise<number>((resolve) => {
    releaseWrite = resolve;
  });
  const writes: Buffer[] = [];
  const stream: NativeFailedByteStream = {
    async read() {
      throw new Error("poisoned exchange must not read ACK");
    },
    async write(bytes) {
      writes.push(Buffer.from(bytes));
      markWriteStarted();
      return heldWrite;
    },
  };
  const exchange = createNativeFailedStreamExchange(stream, key);
  const frame = request(3, Buffer.alloc(150 * 1024, 7));
  const pending = exchange(frame);
  await writeStarted;
  await expect(exchange(frame)).rejects.toThrow("handoff-stream-overlap");
  releaseWrite(writes[0].length);
  await expect(pending).rejects.toThrow("handoff-stream-closed");
  expect(writes).toHaveLength(1);
});

test("malformed outbound request is rejected before writing", async () => {
  const stream = new MemoryStream(Buffer.alloc(0));
  const exchange = createNativeFailedStreamExchange(stream, key);
  await expect(exchange(Buffer.alloc(45))).rejects.toThrow(
    "handoff-stream-request",
  );
  expect(stream.writes).toHaveLength(0);
  await expect(exchange(Buffer.alloc(45))).rejects.toThrow(
    "handoff-stream-closed",
  );
});
