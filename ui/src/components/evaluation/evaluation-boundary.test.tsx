// @vitest-environment jsdom
import { act, StrictMode, useEffect, useState } from "react";
import { expect, test, vi } from "vitest";

import { ApiError } from "@/lib/api/fetch";

import { renderComponent } from "@/test-utils/render";
const mocks = vi.hoisted(() => ({
  scope: { userId: "u", workspaceId: "one" },
  revision: 1,
  grants: ["evaluation.read", "evaluation.manage"],
}));
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
vi.mock("@/providers/auth-provider", () => ({
  useAuth: () => ({ user: { id: "u" }, loading: false }),
}));
vi.mock("@/providers/client-data-provider", () => ({
  useClientDataScope: () => ({ scope: mocks.scope, scopeRevision: mocks.revision }),
}));
vi.mock("@/hooks/use-capabilities", () => ({
  useCapabilities: () => ({ snapshot: { grants: mocks.grants }, loading: false }),
}));
import {
  type EvaluationAccess,
  EvaluationBoundary,
  useEvaluationTask,
} from "./evaluation-boundary";
const jobs: {
  resolve: (v: string) => void;
  reject: (e: unknown) => void;
  workspace?: string;
  signal?: AbortSignal | null;
}[] = [];
function Probe({ access }: { access: EvaluationAccess }) {
  const task = useEvaluationTask(access);
  const { run: runTask } = task;
  const [body, setBody] = useState("");
  useEffect(() => {
    void runTask(
      (o) =>
        new Promise<string>((resolve, reject) =>
          jobs.push({ resolve, reject, workspace: o.workspaceId, signal: o.signal }),
        ),
      setBody,
    );
  }, [runTask]);
  return (
    <div>
      {task.pending ? "pending" : (task.error ?? body)}
      <button
        onClick={() => {
          void runTask(async () => {
            throw new ApiError(403, "forbidden");
          }, setBody);
        }}
      >
        deny
      </button>
    </div>
  );
}
const app = () => <EvaluationBoundary>{(access) => <Probe access={access} />}</EvaluationBoundary>;
test.each(["success", "failure"])("scope switch rejects old %s and its finalizer", async (kind) => {
  jobs.length = 0;
  mocks.scope = { userId: "u", workspaceId: "one" };
  mocks.revision++;
  const view = await renderComponent(app());
  const oldMain = view.container.querySelector("main");
  expect(view.container.querySelectorAll("main")).toHaveLength(1);
  expect(jobs[0].workspace).toBe("one");
  mocks.scope = { userId: "u", workspaceId: "two" };
  mocks.revision++;
  await act(async () => view.root.render(app()));
  expect(view.container.querySelectorAll("main")).toHaveLength(1);
  expect(oldMain?.isConnected).toBe(false);
  expect(jobs[0].signal?.aborted).toBe(true);
  expect(jobs[1].workspace).toBe("two");
  await act(async () => {
    if (kind === "success") jobs[0].resolve("SECRET OLD");
    else jobs[0].reject(new Error("OLD ERROR"));
  });
  expect(view.container.textContent).toBe("pendingdeny");
  await act(async () => jobs[1].resolve("current"));
  expect(view.container.textContent).toBe("currentdeny");
  await act(async () => view.container.querySelector("button")!.click());
  expect(view.container.textContent).toBe("denied");
  expect(view.container.querySelector("main")).toBeNull();
  await view.unmount();
});

test("StrictMode effect replay starts a new scoped read after aborting the first", async () => {
  jobs.length = 0;
  const view = await renderComponent(
    <StrictMode>
      <Probe access={{ workspaceId: "one", canManage: true, canRun: false, canRegister: false }} />
    </StrictMode>,
  );
  expect(jobs).toHaveLength(2);
  expect(jobs[0].signal?.aborted).toBe(true);
  await act(async () => jobs[1].resolve("current"));
  expect(view.container.textContent).toBe("currentdeny");
  await view.unmount();
});
