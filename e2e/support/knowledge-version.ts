import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { randomUUID } from "node:crypto";
import type { Page } from "@playwright/test";
import { expect, appApi } from "../fixtures/acceptance.fixture";
import { registerCleanupAction } from "./cleanup-journal";
import { createOwnedActor } from "./owned-actor";
import { readChatStream, waitForTerminalProfile } from "./execution";
import { pollProjection } from "./poll";
import { acceptanceId } from "./ids";

/** Genuine KB versions and citation reads. Parent soft-delete is accessibility loss, not version purge. */
export async function verifyFixedKnowledgeVersion(
  page: Page,
  modelId: string,
): Promise<void> {
  const team = (
    await appApi<{ id: string }>(page, "/teams", {
      method: "POST",
      body: {
        name: acceptanceId("fixed-knowledge"),
        description: "Owned fixed-version authority",
      },
    })
  ).data;
  registerCleanupAction({
    action: "delete-resource",
    resource: "team",
    resource_id: team.id,
  });
  const actor = await createOwnedActor(page, team.id);
  const operatorId = (await appApi<{ id: string }>(page, "/auth/me")).data.id;
  await page.evaluate(
    ({ workspaceId, userId }) => {
      localStorage.setItem("opencitadel-active-workspace", workspaceId);
      localStorage.setItem(
        `opencitadel-active-workspace:${encodeURIComponent(userId)}`,
        workspaceId,
      );
    },
    { workspaceId: team.id, userId: operatorId },
  );
  const journal = (
    resource: "file" | "knowledge-base" | "session",
    id: string,
  ) =>
    registerCleanupAction({
      action: "delete-resource",
      resource,
      resource_id: id,
      workspace_id: team.id,
    });
  async function upload(content: string): Promise<string> {
    const result = await page.evaluate(
      async ({ content, team }) => {
        const csrf = document.cookie
          .split("; ")
          .find((item) => /^(?:__Host-)?csrf_token=/.test(item))
          ?.split("=")
          .slice(1)
          .join("=");
        const form = new FormData();
        form.append(
          "file",
          new File([content], "fixed-version.md", { type: "text/markdown" }),
        );
        const response = await fetch("/api/files", {
          method: "POST",
          credentials: "include",
          headers: {
            "X-Workspace-Id": team,
            ...(csrf ? { "X-CSRF-Token": decodeURIComponent(csrf) } : {}),
          },
          body: form,
        });
        return { status: response.status, body: await response.json() };
      },
      { content, team: team.id },
    );
    expect(result.status).toBe(200);
    journal("file", result.body.data.id);
    return result.body.data.id;
  }
  try {
    const firstFile = await upload(
      readFileSync(
        resolve(__dirname, "../fixtures/knowledge/acceptance-handbook.md"),
        "utf8",
      ),
    );
    const kb = (
      await appApi<{ id: string }>(page, "/knowledge-bases", {
        method: "POST",
        body: { name: acceptanceId("fixed-version"), settings: {} },
      })
    ).data;
    journal("knowledge-base", kb.id);
    async function addAndWait(
      file: string,
      previous?: string,
    ): Promise<string> {
      await appApi(page, `/knowledge-bases/${kb.id}/documents`, {
        method: "POST",
        body: { file_ids: [file], urls: [], source_type: "upload" },
      });
      const history = await pollProjection(
        () =>
          appApi<any>(page, `/knowledge-bases/${kb.id}/versions`).then(
            (r) => r.data,
          ),
        (value) =>
          value.active_version_id !== previous &&
          value.versions.some(
            (v: any) =>
              v.id === value.active_version_id &&
              ["ready", "degraded"].includes(v.state),
          ),
        {
          timeout: 180_000,
          message: "actual owned knowledge version publication",
        },
      );
      return history.active_version_id;
    }
    const v1 = await addAndWait(firstFile);
    const session = (
      await appApi<any>(page, `/knowledge-bases/${kb.id}/sessions`, {
        method: "POST",
        body: { mode: "ask", model_id: modelId, knowledge_base_version_id: v1 },
      })
    ).data.session_id;
    journal("session", session);
    await readChatStream(page, session, {
      message:
        "[acceptance:evaluation:citation] What is the Citadel verification beacon and its rotation interval?",
      mode: "ask",
      model_id: modelId,
      request_id: randomUUID(),
    });
    await waitForTerminalProfile(page, session, "completed");
    const run = (
      await appApi<any>(
        page,
        `/execution-runs?source_entity_type=session&source_entity_id=${session}`,
      )
    ).data.items[0].run_id;
    const view = await pollProjection(
      () =>
        appApi<any>(page, `/execution-runs/${run}/view`).then((r) => r.data),
      (value) =>
        value.steps.some((step: any) =>
          step.citation_refs?.some(
            (ref: any) =>
              ref.knowledge_base_id === kb.id && ref.version_id === v1,
          ),
        ),
      { timeout: 60_000, message: "persisted exact fixed knowledge citation" },
    );
    const ref = view.steps
      .flatMap((step: any) => step.citation_refs ?? [])
      .find(
        (item: any) =>
          item.knowledge_base_id === kb.id && item.version_id === v1,
      );
    expect(ref.document_revision_id).toBeTruthy();
    expect(ref.doc_id).toBeTruthy();
    const path = `/execution-sources/${encodeURIComponent(ref.citation_id)}/content?locator=document`;
    const original = (await appApi<any>(page, path)).data;
    const exactDocument = `/knowledge-bases/${kb.id}/versions/${v1}/documents/${ref.doc_id}/content`;
    const originalDocument = (await appApi<any>(page, exactDocument)).data;
    expect(original.availability).toBe("available");
    expect(original.content).toContain("cobalt-17");
    const secondFile = await upload(
      "# Owned successor\n\nA newly added successor document says amber-29 every 91 minutes.",
    );
    const v2 = await addAndWait(secondFile, v1);
    expect(v2).not.toBe(v1);
    const fixed = (await appApi<any>(page, path)).data;
    expect(fixed).toEqual(original);
    expect((await appApi<any>(page, exactDocument)).data).toEqual(
      originalDocument,
    );
    const native = `/runs/${run}?at=${encodeURIComponent(view.at)}&citation=${encodeURIComponent(ref.citation_id)}&panel=source`;
    for (const target of [page, actor.page]) {
      await target.goto(native);
      await target
        .getByRole("button", { name: /^Load source$|^加载引用$/ })
        .click();
      await expect(target.locator("[data-execution-source]")).toContainText(v1);
      await expect(target.locator("[data-execution-source]")).toContainText(
        "cobalt-17",
      );
      await expect(target.locator("[data-execution-source]")).not.toContainText(
        "amber-29",
      );
      await target.locator('[data-source-fallback="document"]').click();
      await target
        .getByRole("button", { name: /^Load source$|^加载引用$/ })
        .click();
      await expect(target.locator("[data-execution-source]")).toContainText(v1);
      await expect(target.locator("[data-execution-source]")).toContainText(
        "cobalt-17",
      );
    }
    await appApi(
      page,
      `/teams/${team.id}/members/${actor.cleanup.resource_id}`,
      { method: "DELETE" },
    );
    await appApi(actor.page, path, {
      headers: { "X-Workspace-Id": team.id },
      expectStatus: 403,
    });
    await actor.page.reload();
    await expect(actor.page.locator("body")).not.toContainText("cobalt-17");
    expect((await appApi<any>(page, path)).data).toEqual(original);
    await appApi(page, `/knowledge-bases/${kb.id}`, { method: "DELETE" });
    const unavailable = await appApi<any>(page, path, {
      expectStatus: [200, 404],
    });
    if (unavailable.status === 200) {
      expect(unavailable.data.availability).not.toBe("available");
      expect(unavailable.data.content ?? "").not.toContain("cobalt-17");
    }
    await page.reload();
    await expect(page.locator("[data-execution-source]")).not.toContainText(
      "cobalt-17",
    );
    await expect(page.locator("body")).not.toContainText("amber-29");
  } finally {
    await actor.context.close();
    await page.evaluate((userId) => {
      localStorage.removeItem("opencitadel-active-workspace");
      localStorage.removeItem(
        `opencitadel-active-workspace:${encodeURIComponent(userId)}`,
      );
    }, operatorId);
  }
}
