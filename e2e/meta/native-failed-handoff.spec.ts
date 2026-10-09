import { test, expect } from "@playwright/test";
import { createHash } from "node:crypto";
import fixture from "../performance/native-wire-fixture-v1.json";
import { NativeFailedHandoffClient } from "../performance/native-failed-handoff";
import type { NativeCollector } from "../performance/native-collector";

const command = fixture.command;
const key = "00".repeat(32);

function ack(request: Buffer, payload: Buffer): Buffer {
  const response = Buffer.from(request.subarray(0, 45));
  response.writeUInt8(4, 4);
  response.writeUInt32BE(payload.length, 41);
  return Buffer.concat([response, payload]);
}

test("record frame is exact and only the host ACK sequence is returned", async () => {
  const captured: Buffer[] = [];
  const client = new NativeFailedHandoffClient(command, key, async (frame) => {
    captured.push(Buffer.from(frame));
    const sequence = Buffer.alloc(4);
    sequence.writeUInt32BE(1);
    return ack(frame, sequence);
  });
  expect(await client.emit(Buffer.from("abc\n"))).toBe(1);
  expect(captured[0]).toEqual(
    Buffer.concat([
      Buffer.from("OC3B\x01\x00\x00\x00\x01", "binary"),
      Buffer.alloc(32),
      Buffer.from([0, 0, 0, 4]),
      Buffer.from("abc\n"),
    ]),
  );
});

test("lost or wrong ACK poisons the one-command session", async () => {
  let calls = 0;
  const lost = new NativeFailedHandoffClient(command, key, async () => {
    calls++;
    throw new Error("lost-ack");
  });
  await expect(lost.emit(Buffer.from("abc\n"))).rejects.toThrow("lost-ack");
  await expect(lost.emit(Buffer.from("abc\n"))).rejects.toThrow(
    "handoff-closed",
  );
  expect(calls).toBe(1);

  const wrong = new NativeFailedHandoffClient(command, key, async (frame) => {
    const response = ack(frame, Buffer.alloc(4));
    response.writeUInt32BE(99, 5);
    return response;
  });
  await expect(wrong.emit(Buffer.from("abc\n"))).rejects.toThrow(
    "handoff-ack-owner",
  );
  await expect(wrong.emit(Buffer.from("abc\n"))).rejects.toThrow(
    "handoff-closed",
  );
});

test("callback preserves raw bytes and terminal calls the producer barrier", async () => {
  const frames: Buffer[] = [];
  const snapshot = Buffer.from('{"wire_version":2,"disposition":"partial"}');
  let barrier = 0;
  const collector = {
    terminalFailureSnapshotBytes(received: unknown) {
      expect(received).toBe(command);
      barrier++;
      return Buffer.from(snapshot);
    },
    failureSnapshotBytes() {
      throw new Error("diagnostic snapshot must not be used");
    },
  } as unknown as NativeCollector;
  const client = new NativeFailedHandoffClient(command, key, async (frame) => {
    frames.push(Buffer.from(frame));
    if (frame.readUInt8(4) === 2) {
      return ack(frame, Buffer.from('{"durable_receipt_id":"host"}'));
    }
    return ack(frame, createHash("sha256").update(snapshot).digest());
  });
  const bytes = Buffer.from([0, 255, 10, 0]);
  const receipt = await client.emitFailureChunk({
    ...Object.fromEntries(
      [
        "attempt_id",
        "protocol_id",
        "sample_id",
        "action_id",
        "context_id",
        "page_id",
        "window_id",
        "clock_id",
      ].map((name) => [name, command[name as keyof typeof command]]),
    ),
    wire_version: 2,
    artifact_id: "artifact",
    offset: 0,
    bytes: bytes.length,
    data: bytes,
  } as any);
  expect(receipt.durable_receipt_id).toBe("host");
  const callback = frames[0].subarray(45);
  const metadataBytes = callback.readUInt32BE(0);
  expect(callback.subarray(4 + metadataBytes)).toEqual(bytes);
  expect(await client.terminalFromCollector(collector)).toEqual(snapshot);
  expect(frames[1].subarray(45)).toEqual(snapshot);
  expect(barrier).toBe(1);
  await expect(client.emit(Buffer.from("abc\n"))).rejects.toThrow(
    "handoff-closed",
  );
});

test("truncated ACK and wrong command fail closed", async () => {
  expect(
    () =>
      new NativeFailedHandoffClient(command, "A".repeat(64), async () =>
        Buffer.alloc(0),
      ),
  ).toThrow("handoff-command-key");
  const client = new NativeFailedHandoffClient(command, key, async () =>
    Buffer.alloc(3),
  );
  await expect(client.emit(Buffer.from("abc\n"))).rejects.toThrow(
    "handoff-ack-truncated",
  );
  await expect(client.emit(Buffer.from("abc\n"))).rejects.toThrow(
    "handoff-closed",
  );
});
