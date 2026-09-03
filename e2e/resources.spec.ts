import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import type { Page } from "@playwright/test";

import type { components } from "../ui/src/lib/api/generated/schema";
import { appApi, expect, test } from "./fixtures/acceptance.fixture";
import { registerCleanupAction } from "./support/cleanup-journal";
import { acceptanceId } from "./support/ids";
import { pollProjection } from "./support/poll";

type FileInfo = components["schemas"]["File"];
type KnowledgeBase = components["schemas"]["KnowledgeBaseResponse"];
type KnowledgeVersions = components["schemas"]["ListKnowledgeVersionsResponse"];
type KnowledgeVersion = components["schemas"]["KnowledgeVersionResponse"];
type KnowledgeDocuments =
  components["schemas"]["ListKnowledgeDocumentsResponse"];
type KnowledgeContent = components["schemas"]["ReadKnowledgeDocumentResponse"];
type KnowledgeGraph = components["schemas"]["KnowledgeGraphResponse"];
type KnowledgeSession =
  components["schemas"]["CreateKnowledgeBaseSessionResponse"];
type ResourceBinding = components["schemas"]["ResourceBindingResponse"];

const handbookPath = resolve(
  __dirname,
  "fixtures/knowledge/acceptance-handbook.md",
);
const handbookFact =
  "The Citadel verification beacon is cobalt-17 and rotates every 37 minutes.";
const runIdPattern =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function cover(...requirementIds: string[]): void {
  for (const requirementId of requirementIds) {
    test
      .info()
      .annotations.push({ type: "acceptance", description: requirementId });
  }
}

async function uploadFixture(
  page: Page,
  path: string,
  filename: string,
  mimeType: string,
): Promise<FileInfo> {
  const content = readFileSync(path, "utf8");
  const result = await page.evaluate(
    async ({ content, filename, mimeType }) => {
      const cookies = document.cookie.split("; ");
      const csrf = (
        cookies.find((cookie) => cookie.startsWith("__Host-csrf_token=")) ??
        cookies.find((cookie) => cookie.startsWith("csrf_token="))
      )
        ?.split("=")
        .slice(1)
        .join("=");
      const workspaceId = window.localStorage.getItem(
        "opencitadel-active-workspace",
      );
      const body = new FormData();
      body.append("file", new File([content], filename, { type: mimeType }));
      const response = await fetch("/api/files", {
        method: "POST",
        credentials: "include",
        headers: {
          Accept: "application/json",
          ...(csrf ? { "X-CSRF-Token": decodeURIComponent(csrf) } : {}),
          ...(workspaceId ? { "X-Workspace-Id": workspaceId } : {}),
        },
        body,
      });
      return { status: response.status, payload: await response.json() };
    },
    { content, filename, mimeType },
  );
  expect(result.status).toBe(200);
  expect(result.payload).toMatchObject({ code: 200, msg: "success" });
  return result.payload.data as FileInfo;
}

async function knowledgeHistory(
  page: Page,
  knowledgeBaseId: string,
): Promise<KnowledgeVersions> {
  return (
    await appApi<KnowledgeVersions>(
      page,
      `/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/versions`,
    )
  ).data;
}

async function observeKnowledgeCandidate(
  page: Page,
  knowledgeBaseId: string,
  versionId?: string,
): Promise<KnowledgeVersion> {
  const history = await pollProjection(
    () => knowledgeHistory(page, knowledgeBaseId),
    (projection) => {
      const version = versionId
        ? projection.versions?.find((item) => item.id === versionId)
        : projection.versions?.[0];
      return Boolean(version?.build?.run_id);
    },
    {
      timeout: 60_000,
      message: "knowledge candidate receives a formal execution Run",
    },
  );
  const candidate = versionId
    ? history.versions?.find((item) => item.id === versionId)
    : history.versions?.[0];
  if (!candidate?.build?.run_id) {
    throw new Error("knowledge candidate Run projection is missing");
  }
  expect(candidate.build.run_id).toMatch(runIdPattern);
  expect(candidate.build.id).toBe(candidate.build_id);
  expect(candidate.build.version_id).toBe(candidate.id);
  return candidate;
}

async function waitForKnowledgePublication(
  page: Page,
  knowledgeBaseId: string,
  versionId: string,
): Promise<KnowledgeVersion> {
  const history = await pollProjection(
    () => knowledgeHistory(page, knowledgeBaseId),
    (projection) => {
      const version = projection.versions?.find(
        (item) => item.id === versionId,
      );
      return (
        projection.active_version_id === versionId &&
        Boolean(version?.is_active) &&
        Boolean(version?.is_published) &&
        ["ready", "degraded"].includes(version?.state ?? "") &&
        version?.build?.status === "completed"
      );
    },
    {
      timeout: 180_000,
      intervals: [250, 500, 1_000, 2_000],
      message: `knowledge version ${versionId} becomes active`,
    },
  );
  const published = history.versions?.find((item) => item.id === versionId);
  if (!published?.build?.run_id) {
    throw new Error("published knowledge version is missing its Run");
  }
  expect(published.build.progress).toBe(100);
  expect(published.build.terminal_at).toBeTruthy();
  expect(published.is_candidate).toBe(false);
  return published;
}

test.describe.configure({ mode: "serial" });

test("knowledge candidates publish immutable versions and sessions remain pinned", async ({
  operatorPage: page,
}) => {
  test.setTimeout(300_000);
  cover("KB-BUILD", "KB-PUBLISH", "KB-PIN", "KB-DEGRADED");

  const uploaded = await uploadFixture(
    page,
    handbookPath,
    "acceptance-handbook.md",
    "text/markdown",
  );
  expect(uploaded.filename).toBe("acceptance-handbook.md");

  const knowledgeBase = (
    await appApi<KnowledgeBase>(page, "/knowledge-bases", {
      method: "POST",
      body: { name: acceptanceId("knowledge"), settings: {} },
    })
  ).data;
  registerCleanupAction({
    action: "delete-resource",
    resource: "knowledge-base",
    resource_id: knowledgeBase.id,
  });
  expect(knowledgeBase.active_version_id).toBeNull();
  expect((await knowledgeHistory(page, knowledgeBase.id)).versions).toEqual([]);

  await appApi<KnowledgeBase>(
    page,
    `/knowledge-bases/${encodeURIComponent(knowledgeBase.id)}/documents`,
    {
      method: "POST",
      body: { file_ids: [uploaded.id], urls: [], source_type: "upload" },
    },
  );
  const firstCandidate = await observeKnowledgeCandidate(
    page,
    knowledgeBase.id,
  );
  const first = await waitForKnowledgePublication(
    page,
    knowledgeBase.id,
    firstCandidate.id,
  );
  expect(first.parent_version_id).toBeNull();
  expect(first.state).toBe("degraded");
  expect(first.capabilities).toMatchObject({
    keyword_search: true,
    vector_search: true,
    graph_search: false,
  });
  expect(first.degraded_reasons).toContain("GRAPH_UNAVAILABLE");

  const currentKnowledge = (
    await appApi<KnowledgeBase>(
      page,
      `/knowledge-bases/${encodeURIComponent(knowledgeBase.id)}`,
    )
  ).data;
  expect(currentKnowledge.active_version_id).toBe(first.id);
  expect(currentKnowledge.status).toBe("ready");
  expect(currentKnowledge.error).toContain("GRAPH_UNAVAILABLE");

  const documents = (
    await appApi<KnowledgeDocuments>(
      page,
      `/knowledge-bases/${encodeURIComponent(knowledgeBase.id)}/documents`,
    )
  ).data;
  expect(documents.total).toBe(1);
  const document = documents.documents?.[0];
  if (!document) throw new Error("published knowledge document is missing");
  expect(document.status).toBe("ready");
  const content = (
    await appApi<KnowledgeContent>(
      page,
      `/knowledge-bases/${encodeURIComponent(knowledgeBase.id)}/versions/${encodeURIComponent(first.id)}/documents/${encodeURIComponent(document.id)}/content?limit=200`,
    )
  ).data;
  expect(content.version_id).toBe(first.id);
  expect(content.document_revision_id).toBeTruthy();
  expect(content.items?.map((item) => item.content).join("\n")).toContain(
    handbookFact,
  );
  const graph = (
    await appApi<KnowledgeGraph>(
      page,
      `/knowledge-bases/${encodeURIComponent(knowledgeBase.id)}/versions/${encodeURIComponent(first.id)}/graph`,
    )
  ).data;
  expect(graph).toMatchObject({ capability: false, nodes: [], edges: [] });

  await page.goto("/knowledge");
  await expect(
    page.getByText(knowledgeBase.name, { exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/GRAPH_UNAVAILABLE/).first()).toBeVisible();

  const session = (
    await appApi<KnowledgeSession>(
      page,
      `/knowledge-bases/${encodeURIComponent(knowledgeBase.id)}/sessions`,
      {
        method: "POST",
        body: {
          mode: "ask",
          knowledge_base_version_id: first.id,
        },
      },
    )
  ).data;
  registerCleanupAction({
    action: "delete-resource",
    resource: "session",
    resource_id: session.session_id,
  });
  const initialBindings = (
    await appApi<ResourceBinding[]>(
      page,
      `/sessions/${encodeURIComponent(session.session_id)}/resource-bindings`,
    )
  ).data;
  expect(initialBindings).toEqual([
    expect.objectContaining({
      resource_kind: "knowledge_base",
      resource_id: knowledgeBase.id,
      version_id: first.id,
      is_current: true,
    }),
  ]);

  const secondCandidate = (
    await appApi<KnowledgeVersion>(
      page,
      `/knowledge-bases/${encodeURIComponent(knowledgeBase.id)}/builds`,
      { method: "POST" },
    )
  ).data;
  expect(secondCandidate).toMatchObject({
    parent_version_id: first.id,
    is_candidate: true,
    is_published: false,
  });
  await observeKnowledgeCandidate(page, knowledgeBase.id, secondCandidate.id);
  const second = await waitForKnowledgePublication(
    page,
    knowledgeBase.id,
    secondCandidate.id,
  );
  expect(second.parent_version_id).toBe(first.id);
  expect(second.id).not.toBe(first.id);

  const pinnedBindings = (
    await appApi<ResourceBinding[]>(
      page,
      `/sessions/${encodeURIComponent(session.session_id)}/resource-bindings`,
    )
  ).data;
  expect(pinnedBindings).toEqual([
    expect.objectContaining({
      resource_kind: "knowledge_base",
      resource_id: knowledgeBase.id,
      version_id: first.id,
      is_current: true,
    }),
  ]);
});
