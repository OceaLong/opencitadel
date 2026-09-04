import { describe, expect, it } from "vitest";

import type { SSEEventData } from "@/lib/api/types";

import { createSessionEventStore } from "./event-store";

/** 按后端口径构造游标：base64url(8 字节大端序号 + 16 字节签名)。 */
function cursorFor(seq: number): string {
  const bytes = new Uint8Array(24);
  const view = new DataView(bytes.buffer);
  view.setBigUint64(0, BigInt(seq));
  // 签名部分内容与排序无关，用可辨识的填充即可。
  bytes.fill(0xab, 8);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function event(seq: number): SSEEventData {
  return {
    type: "message",
    data: {
      event_id: cursorFor(seq),
      created_at: 1_788_500_000,
      schema_version: 1,
      visibility: "user",
      channel: "ui",
      persist: true,
      role: "assistant",
      message: `event-${seq}`,
    },
  } as unknown as SSEEventData;
}

describe("event store cursor ordering", () => {
  it("keeps numeric order across base64 alphabet boundaries", () => {
    // 低位 6-bit 分组跨越 'z'→'0'（值 51→52）与 '9'→'-'（61→62）边界时，
    // 游标字符串的字典序与序号数值序相反 —— 排序必须按解码后的序号。
    const store = createSessionEventStore();
    const seqs = [50, 51, 52, 61, 62, 63, 64, 100, 128];
    // 打乱到达顺序，模拟历史分页与实时流交错。
    for (const seq of [52, 50, 63, 51, 128, 61, 100, 62, 64]) {
      expect(store.append(event(seq))).toBe(true);
    }

    const messages = store.getSnapshot().map((item) => (item.data as { message?: string }).message);
    expect(messages).toEqual(seqs.map((seq) => `event-${seq}`));
  });

  it("appends strictly increasing cursors without reordering", () => {
    const store = createSessionEventStore();
    for (const seq of [1, 2, 3, 200, 201]) store.append(event(seq));

    const messages = store.getSnapshot().map((item) => (item.data as { message?: string }).message);
    expect(messages).toEqual(["event-1", "event-2", "event-3", "event-200", "event-201"]);
  });

  it("deduplicates by event_id", () => {
    const store = createSessionEventStore();
    expect(store.append(event(7))).toBe(true);
    expect(store.append(event(7))).toBe(false);
    expect(store.getSnapshot()).toHaveLength(1);
  });
});
