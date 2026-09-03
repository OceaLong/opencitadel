// @vitest-environment jsdom

import { NextIntlClientProvider } from "next-intl";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";

import en from "../../../../../messages/en.json";

const mocks = vi.hoisted(() => ({
  capability: vi.fn(),
  downloadComplianceReport: vi.fn(),
  getComplianceReport: vi.fn(),
}));

vi.mock("@/hooks/use-capabilities", () => ({
  useCapabilities: () => ({ capability: mocks.capability }),
}));
vi.mock("@/lib/api/compliance", () => ({
  complianceApi: {
    downloadComplianceReport: mocks.downloadComplianceReport,
    getComplianceReport: mocks.getComplianceReport,
  },
}));

import AdminComplianceReportPage from "./page";

function renderPage() {
  return renderComponent(
    <NextIntlClientProvider locale="en" messages={en}>
      <AdminComplianceReportPage />
    </NextIntlClientProvider>,
  );
}

function pdfButton(container: HTMLElement): HTMLButtonElement | undefined {
  return [...container.querySelectorAll("button")].find((b) =>
    b.textContent?.includes(en.compliance.exportPdf),
  );
}

afterEach(() => {
  document.body.replaceChildren();
  vi.clearAllMocks();
});

describe("AdminComplianceReportPage PDF gating", () => {
  it("offers an enabled PDF export button when report_pdf capability is available", async () => {
    mocks.capability.mockImplementation((name: string) =>
      name === "report_pdf" ? { state: "available", details: {} } : undefined,
    );

    const { container, unmount } = await renderPage();

    const button = pdfButton(container);
    expect(button).toBeTruthy();
    expect(button?.disabled).toBe(false);
    // 导出改为 authenticatedFetch + Blob，不再渲染直连后端的 <a href>。
    expect(container.querySelector('a[href*="format=pdf"]')).toBeNull();
    await unmount();
  });

  it("disables the PDF export button when report_pdf is not available", async () => {
    mocks.capability.mockImplementation((name: string) =>
      name === "report_pdf"
        ? {
            state: "not_configured",
            reason_key: "capabilities.reason.pdfRendererUnavailable",
            details: {},
          }
        : undefined,
    );

    const { container, unmount } = await renderPage();

    expect(pdfButton(container)?.disabled).toBe(true);
    await unmount();
  });
});
