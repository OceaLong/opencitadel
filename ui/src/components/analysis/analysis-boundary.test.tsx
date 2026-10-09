// @vitest-environment jsdom
import { afterEach, expect, test, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";

import { AnalysisBoundary } from "./analysis-boundary";
const state = vi.hoisted(() => ({ role: "member", globalRole: "user", workspaceId: "team" }));
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
vi.mock("@/providers/auth-provider", () => ({
  useAuth: () => ({ user: { id: "u", global_role: state.globalRole }, loading: false }),
}));
vi.mock("@/providers/client-data-provider", () => ({
  useClientDataScope: () => ({
    scope: { userId: "u", workspaceId: state.workspaceId },
    scopeRevision: 1,
  }),
}));
vi.mock("@/hooks/use-capabilities", () => ({
  useCapabilities: () => ({ snapshot: { grants: ["execution.read"] }, loading: false }),
}));
vi.mock("@/lib/api/team", () => ({
  teamApi: { members: async () => ({ members: [{ user_id: "u", role: state.role }] }) },
}));
afterEach(() => {
  document.body.replaceChildren();
  state.globalRole = "user";
  state.workspaceId = "team";
});
test.each([
  ["owner", true],
  ["admin", true],
  ["member", false],
])("preference capability uses scoped %s role", async (role, expected) => {
  state.role = role;
  const view = await renderComponent(
    <AnalysisBoundary>
      {(a) => <button disabled={!a.canWritePreferences}>save</button>}
    </AnalysisBoundary>,
  );
  expect(view.container.querySelector("button")!.disabled).toBe(!expected);
  await view.unmount();
});
test("auditor cannot write even with team owner role", async () => {
  state.role = "owner";
  state.globalRole = "auditor";
  const view = await renderComponent(
    <AnalysisBoundary>
      {(a) => <button disabled={!a.canWritePreferences}>save</button>}
    </AnalysisBoundary>,
  );
  expect(view.container.querySelector("button")!.disabled).toBe(true);
  await view.unmount();
});
test("personal workspace user can write own preference", async () => {
  state.workspaceId = "";
  const view = await renderComponent(
    <AnalysisBoundary>
      {(a) => <button disabled={!a.canWritePreferences}>save</button>}
    </AnalysisBoundary>,
  );
  expect(view.container.querySelector("button")!.disabled).toBe(false);
  await view.unmount();
});
