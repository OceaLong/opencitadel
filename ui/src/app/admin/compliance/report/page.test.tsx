// @vitest-environment jsdom

import { NextIntlClientProvider } from "next-intl";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";

import en from "../../../../../messages/en.json";

const mocks = vi.hoisted(() => ({
  capability: vi.fn(),
  complianceReportUrl: vi.fn((p: { format?: string }) => `/api/report?format=${p.format ?? "md"}`),
  getComplianceReport: vi.fn(),
}));

vi.mock("@/hooks/use-capabilities", () => ({
  useCapabilities: () => ({ capability: mocks.capability }),
}));
vi.mock("@/lib/api/compliance", () => ({
  complianceApi: {
    complianceReportUrl: mocks.complianceReportUrl,
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

function pdfAnchor(container: HTMLElement): HTMLAnchorElement | undefined {
  return [...container.querySelectorAll("a")].find((a) =>
    a.getAttribute("href")?.includes("format=pdf"),
  );
}

afterEach(() => {
  document.body.replaceChildren();
  vi.clearAllMocks();
});

describe("AdminComplianceReportPage PDF gating", () => {
  it("offers a real PDF export link when report_pdf capability is available", async () => {
    mocks.capability.mockImplementation((name: string) =>
      name === "report_pdf" ? { state: "available", details: {} } : undefined,
    );

    const { container, unmount } = await renderPage();

    expect(pdfAnchor(container)).toBeTruthy();
    expect(pdfButton(container)?.disabled ?? false).toBe(false);
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

    expect(pdfAnchor(container)).toBeUndefined();
    expect(pdfButton(container)?.disabled).toBe(true);
    await unmount();
  });
});
