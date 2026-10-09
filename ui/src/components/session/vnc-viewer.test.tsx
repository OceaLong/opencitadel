// @vitest-environment jsdom
import { expect, test, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";
const connection = vi.hoisted(() => ({
  disconnect: vi.fn(),
  listeners: new Map<string, (event: unknown) => void>(),
}));
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
vi.mock("@novnc/novnc/lib/rfb", () => ({
  default: class {
    disconnect = connection.disconnect;
    addEventListener(name: string, callback: (event: unknown) => void) {
      connection.listeners.set(name, callback);
    }
  },
}));
import { VNCViewer } from "./vnc-viewer";
test("unmount disconnects RFB and ignores its late connection callbacks", async () => {
  const status = vi.fn();
  const r = await renderComponent(<VNCViewer url="ws://test" onStatusChange={status} />);
  const late = connection.listeners.get("connect")!;
  await r.unmount();
  status.mockClear();
  late({});
  expect(connection.disconnect).toHaveBeenCalledTimes(1);
  expect(status).not.toHaveBeenCalled();
});
