// @vitest-environment jsdom

import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";

const mocks = vi.hoisted(() => ({
  preview: vi.fn(),
  refresh: vi.fn(),
  replace: vi.fn(),
  translate: (key: string) =>
    ({
      identifierPlaceholder: "Email or username",
      passwordPlaceholder: "Password",
      emailPlaceholder: "Email",
      usernamePlaceholder: "Username",
      passwordMinPlaceholder: "Password (min 8 characters)",
    })[key] ?? key,
}));

vi.mock("next-intl", () => ({ useTranslations: () => mocks.translate }));
vi.mock("next/navigation", () => ({
  useParams: () => ({ token: "invite-token" }),
  useRouter: () => ({ push: vi.fn(), replace: mocks.replace }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/api/auth", () => ({
  authApi: { login: vi.fn() },
}));
vi.mock("@/lib/api/team", () => ({
  teamApi: {
    preview: mocks.preview,
    registerAndAccept: vi.fn(),
    accept: vi.fn(),
  },
}));
vi.mock("@/providers/auth-provider", () => ({
  useAuth: () => ({ user: null, loading: false, refresh: mocks.refresh }),
}));
vi.mock("@/providers/client-data-provider", () => ({
  useClientDataScope: () => ({ setWorkspaceId: vi.fn() }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import AcceptInvitationPage from "./invitations/[token]/page";
import LoginPage from "./login/page";

function expectNamedInput(container: HTMLElement, id: string, name: string, label: string): void {
  const input = container.querySelector<HTMLInputElement>(`#${id}`);
  expect(input?.name).toBe(name);
  expect(container.querySelector(`label[for="${id}"]`)?.textContent).toBe(label);
}

describe("identity form accessibility", () => {
  afterEach(() => {
    mocks.preview.mockReset();
    mocks.refresh.mockReset();
    mocks.replace.mockReset();
    document.body.replaceChildren();
  });

  it("gives the local sign-in controls durable names and labels", async () => {
    const { container, unmount } = await renderComponent(<LoginPage />);

    expectNamedInput(container, "login-identifier", "identifier", "Email or username");
    expectNamedInput(container, "login-password", "password", "Password");
    await unmount();
  });

  it("gives invitation registration controls durable names and labels", async () => {
    mocks.preview.mockResolvedValue({
      team_id: "team-1",
      team_name: "Acceptance team",
      role: "member",
      status: "pending",
      expires_at: "2026-08-28T00:00:00Z",
      requires_registration: true,
      email_hint: null,
    });
    const { container, unmount } = await renderComponent(<AcceptInvitationPage />);
    await act(async () => {
      await Promise.resolve();
    });

    expectNamedInput(container, "invite-email", "email", "Email");
    expectNamedInput(container, "invite-username", "username", "Username");
    expectNamedInput(container, "invite-password", "password", "Password (min 8 characters)");
    await unmount();
  });
});
