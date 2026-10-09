"use client";
import { useLayoutEffect, useState } from "react";
import { useTranslations } from "next-intl";

import { MarkdownContent } from "@/components/markdown-content";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import {
  type DetailBodyOwner,
  type DetailBodyTarget,
  useExecutionDetailBody,
} from "@/hooks/use-execution-detail-body";
import { executionViewApi } from "@/lib/api/execution-view";
import { ApiError } from "@/lib/api/fetch";
import type { RunView, StepDetail, StepView, ViewPage } from "@/lib/api/types/execution-view";
import { WORKBENCH_PANELS, type WorkbenchSelection } from "@/lib/execution-view/state";
import { useAuth } from "@/providers/auth-provider";
import { useClientDataScope } from "@/providers/client-data-provider";

import { type ApprovalActionContext, ApprovalDetail } from "./approval-detail";
import { ArtifactPanel } from "./artifact-panel";
import { ContentSection, InvocationDetail } from "./invocation-detail";
import { RetainedDetailPanel, type RetainedDetailPanelProps } from "./retained-detail-panel";
import { type SourceLocator, SourcePanel } from "./source-panel";
export type DetailPanelProps = {
  renderReady?: boolean;
  selection: WorkbenchSelection;
  run: RunView | null;
  page: ViewPage | null;
  detail: StepDetail | null;
  onReturnLive: () => void;
  onChanged: () => void;
  onRevoked: () => void;
  onSelectionChange?: (selection: WorkbenchSelection) => void;
  historical?: boolean;
  approvalContext?: ApprovalActionContext;
};
export function DetailPanel(props: DetailPanelProps | { retained: RetainedDetailPanelProps }) {
  return "retained" in props ? (
    <RetainedDetailPanel {...props.retained} />
  ) : (
    <LiveDetailPanel {...props} />
  );
}
function LiveDetailPanel(props: DetailPanelProps) {
  const t = useTranslations("executionDetail");
  const { user, loading } = useAuth();
  const { scope, scopeRevision } = useClientDataScope();
  const { selection, page, detail, run } = props;
  const valid = Boolean(
    !loading &&
    user &&
    scope?.userId === user.id &&
    page?.at &&
    run?.run_id === selection.runId &&
    page.run.run_id === selection.runId &&
    (!selection.stepId ||
      (detail?.step_id === selection.stepId &&
        detail.at === page.at &&
        detail.run_id === selection.runId &&
        detail.projection_revision === page.revision)),
  );
  const key = JSON.stringify([
    user?.id,
    scope?.workspaceId,
    scopeRevision,
    selection.runId,
    page?.at,
    page?.revision,
    selection.stepId,
    detail?.attempt_id,
    selection.artifactId,
    selection.version,
    selection.citationId,
  ]);
  if (!valid || !scope || !page || !page.at || !run)
    return (
      <p role="status" className="p-3 text-sm">
        {t("unavailable")}
      </p>
    );
  return (
    <AuthorizedDetail
      key={key}
      {...props}
      page={page}
      run={run}
      owner={{ key, workspaceId: scope.workspaceId, at: page.at }}
    />
  );
}
function AuthorizedDetail({
  selection,
  run,
  page,
  detail,
  owner,
  onRevoked,
  onReturnLive,
  onSelectionChange,
  historical,
  approvalContext,
  renderReady = false,
}: DetailPanelProps & { page: ViewPage; run: RunView; owner: DetailBodyOwner }) {
  const t = useTranslations("executionDetail");
  const [locator, setLocator] = useState<SourceLocator>("original");
  const [producerId, setProducerId] = useState<string | null>(null);
  const body = useExecutionDetailBody(
    { ...owner, key: JSON.stringify([owner.key, producerId, locator]) },
    onRevoked,
  );
  const kinds = {
    Input: t("kindInput"),
    Output: t("kindOutput"),
    Artifact: t("kindArtifact"),
    Source: t("kindSource"),
  };
  const panels = {
    overview: t("panel.overview"),
    "input-output": t("panel.input-output"),
    approval: t("panel.approval"),
    artifact: t("panel.artifact"),
    source: t("panel.source"),
  };
  const [localPanel, setPanel] = useState(selection.panel ?? "overview");
  const panel = onSelectionChange ? (selection.panel ?? "overview") : localPanel;
  const [producers, setProducers] = useState<StepDetail[] | null>(null);
  const [ownerError, setOwnerError] = useState(false);
  const referenceTarget = Boolean(selection.artifactId || selection.citationId);
  // Resolve every matching producer at this exact cut. Multiple producers require a choice.
  useLayoutEffect(() => {
    if (!referenceTarget || detail) return;
    const controller = new AbortController();
    const options = {
      workspaceId: owner.workspaceId,
      signal: controller.signal,
      skipErrorHandler: true,
    };
    const matches = (step: StepView) =>
      selection.artifactId
        ? step.artifact_refs?.some(
            (ref) =>
              ref.artifact_id === selection.artifactId &&
              ref.version === selection.version &&
              ref.availability === "available",
          )
        : step.citation_refs?.some(
            (ref) => ref.citation_id === selection.citationId && ref.availability === "available",
          );
    void (async () => {
      try {
        const candidates = page.steps.filter(
          (step) => matches(step) || (selection.citationId && Boolean(step.artifact_refs?.length)),
        );
        const canonicalRefs = new Map<string, NonNullable<StepView["citation_refs"]>>();
        const proveCitation = async (step: StepView) => {
          if (!selection.citationId || matches(step)) return step;
          const refs: NonNullable<StepView["citation_refs"]> = [];
          for (const artifact of step.artifact_refs ?? []) {
            const cacheKey = `${artifact.artifact_id}:${artifact.version}:${step.step_id}`;
            let citations = canonicalRefs.get(cacheKey);
            if (!citations) {
              const rows = await executionViewApi.getProvenance(
                artifact.artifact_id,
                artifact.version,
                options,
                { run_id: selection.runId, at: owner.at },
              );
              if (controller.signal.aborted) return step;
              citations = rows
                .filter(
                  (row) =>
                    row.binding_status === "bound" &&
                    row.producer_run_id === step.run_id &&
                    row.producer_step_ids.includes(step.step_id),
                )
                .flatMap((row) => row.citation_refs ?? []);
              canonicalRefs.set(cacheKey, citations);
            }
            refs.push(...citations);
          }
          return { ...step, citation_refs: [...(step.citation_refs ?? []), ...refs] };
        };
        let cursor = page.next_cursor;
        const seen = new Set<string>();
        while (cursor) {
          if (seen.has(cursor)) throw new ApiError(409, "revision_conflict");
          seen.add(cursor);
          const next = await executionViewApi.listSteps(
            selection.runId,
            { at: owner.at, revision: page.revision, cursor },
            options,
          );
          if (controller.signal.aborted) return;
          if (next.at !== page.at || next.revision !== page.revision)
            throw new ApiError(409, "revision_conflict");
          candidates.push(
            ...next.items.filter(
              (step) =>
                matches(step) || (selection.citationId && Boolean(step.artifact_refs?.length)),
            ),
          );
          cursor = next.next_cursor;
        }
        const verified: StepDetail[] = [];
        for (const candidate of candidates) {
          if (verified.some((item) => item.step_id === candidate.step_id)) continue;
          const raw = await executionViewApi.getStep(
            selection.runId,
            candidate.step_id,
            { at: owner.at },
            options,
          );
          if (controller.signal.aborted) return;
          const value = (await proveCitation(raw)) as StepDetail;
          if (controller.signal.aborted) return;
          if (selection.citationId && !matches(value)) continue;
          if (
            value.run_id !== selection.runId ||
            value.step_id !== candidate.step_id ||
            value.at !== page.at ||
            value.projection_revision !== page.revision ||
            value.attempt_id !== candidate.attempt_id ||
            !matches(value)
          )
            throw new ApiError(409, "revision_conflict");
          verified.push(value);
        }
        if (!controller.signal.aborted) setProducers(verified);
      } catch (error) {
        if (controller.signal.aborted) return;
        if (error instanceof ApiError && [401, 403, 404].includes(error.code)) body.revoke();
        else setOwnerError(true);
      }
    })();
    return () => controller.abort();
    // This component is keyed by the complete owner; body state never crosses a selection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [owner.key]);
  const producer =
    detail ??
    (producers?.length === 1
      ? producers[0]
      : producers?.find((item) => item.step_id === producerId)) ??
    null;
  const inputOutput = (kind: "input" | "output"): DetailBodyTarget | null => {
    const ref = detail?.[kind === "input" ? "input_ref" : "output_ref"];
    if (!detail || ref?.availability !== "available") return null;
    const query = { at: owner.at, content_kind: kind, limit_bytes: 65536 } as const;
    return {
      key: `${detail.step_id}:${detail.attempt_id}:${kind}`,
      filename: `${kind}.txt`,
      read: (cursor, options) =>
        executionViewApi.readContent(
          selection.runId,
          detail.step_id,
          { ...query, cursor },
          options,
        ),
      download: (options) =>
        executionViewApi.downloadContent(selection.runId, detail.step_id, query, options),
    };
  };
  const artifact = producer?.artifact_refs?.find(
    (ref) =>
      ref.artifact_id === selection.artifactId &&
      ref.version === selection.version &&
      ref.availability === "available",
  );
  const artifactTarget: DetailBodyTarget | null =
    artifact && producer
      ? {
          key: `artifact:${producer.step_id}:${artifact.artifact_id}:${artifact.version}:presentation`,
          filename: `artifact-${artifact.version}.txt`,
          read: (cursor, options) =>
            executionViewApi.readArtifact(
              artifact.artifact_id,
              {
                version: artifact.version,
                run_id: selection.runId,
                step_id: producer.step_id,
                at: owner.at,
                presentation: true,
                limit_bytes: 65536,
                cursor,
              },
              options,
            ),
          download: (options) =>
            executionViewApi.downloadArtifact(
              artifact.artifact_id,
              {
                version: artifact.version,
                run_id: selection.runId,
                step_id: producer.step_id,
                at: owner.at,
                presentation: false,
                limit_bytes: 65536,
              },
              options,
            ),
        }
      : null;
  const citation = producer?.citation_refs?.find(
    (ref) => ref.citation_id === selection.citationId && ref.availability === "available",
  );
  const sourceTarget: DetailBodyTarget | null = citation
    ? {
        key: `source:${citation.citation_id}:${locator}`,
        filename: citation.resource_kind === "file" ? "source.bin" : "source.txt",
        read: (cursor, options) =>
          executionViewApi.readSource(
            citation.citation_id,
            { cursor, limit_bytes: 65536, ...(locator !== "original" ? { locator } : {}) },
            options,
          ),
        download: (options) =>
          citation.resource_kind === "file"
            ? executionViewApi.downloadFileSource(citation.citation_id, options)
            : executionViewApi.downloadSource(
                citation.citation_id,
                { limit_bytes: 65536, ...(locator !== "original" ? { locator } : {}) },
                options,
              ),
      }
    : null;
  const content = (
    target: DetailBodyTarget | null,
    kind: "Input" | "Output" | "Artifact" | "Source",
  ) =>
    target ? (
      <ContentSection
        key={target.key}
        state={body.items[target.key]}
        kind={kind}
        onLoad={() => void body.load(target)}
        onDownload={() => void body.download(target)}
      />
    ) : (
      <p className="text-muted-foreground text-xs">{t("unavailableKind", { kind: kinds[kind] })}</p>
    );
  if (body.denied)
    return (
      <p role="status" className="p-3 text-sm">
        {t("unavailable")}
      </p>
    );
  const relatedApprovals = (page.approvals ?? []).filter(
    (approval) => !detail || approval.subject_activity_id === detail.activity_id,
  );
  return (
    <div
      className="space-y-3 overflow-auto p-3 text-sm"
      data-execution-detail
      data-native-view="detail"
      data-native-ready={renderReady && !ownerError}
      data-public-scope={owner.workspaceId}
      data-public-run={run.run_id}
      data-public-at={page.at}
      data-public-revision={page.revision}
      data-public-step={detail?.step_id}
    >
      <h2
        key={JSON.stringify([page.at, page.revision, detail?.step_id])}
        {...{ elementtiming: "execution-detail" }}
        data-native-content="detail-identity"
        className="font-medium break-all"
      >
        {selection.artifactId ?? selection.citationId ?? detail?.step_id ?? run.run_id}
      </h2>
      {selection.version != null && (
        <p className="text-xs">{t("version", { version: selection.version })}</p>
      )}
      {(historical ?? selection.at !== null) && (
        <Button size="sm" variant="outline" onClick={onReturnLive}>
          {t("returnLive")}
        </Button>
      )}
      {detail ? (
        <InvocationDetail detail={detail} />
      ) : run.public_summary ? (
        <MarkdownContent content={run.public_summary} />
      ) : null}
      <Tabs
        value={panel}
        onValueChange={(value) => {
          setPanel(value as typeof panel);
          onSelectionChange?.({ ...selection, panel: value as typeof panel });
        }}
      >
        <TabsList className="flex h-auto flex-wrap">
          {WORKBENCH_PANELS.map((value) => (
            <TabsTrigger key={value} value={value}>
              {panels[value]}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="overview">
          <p className="text-muted-foreground text-xs">{t("summaryOnly")}</p>
        </TabsContent>
        <TabsContent value="input-output" className="space-y-3">
          {content(inputOutput("input"), "Input")}
          {content(inputOutput("output"), "Output")}
        </TabsContent>
        <TabsContent value="approval" className="space-y-3">
          {relatedApprovals.map((approval) => (
            <ApprovalDetail
              key={approval.approval_id}
              approval={approval}
              runId={selection.runId}
              workspaceId={owner.workspaceId}
              context={approvalContext}
              onRevoked={body.revoke}
            />
          ))}
          {relatedApprovals.length === 0 && <p>{t("notRecorded")}</p>}
        </TabsContent>
        <TabsContent value="artifact" className="space-y-3">
          <ArtifactPanel
            runId={selection.runId}
            at={owner.at}
            owner={owner}
            artifacts={Array.from(
              new Map(
                [
                  ...(page.artifacts ?? []),
                  ...(detail?.artifact_refs ?? []),
                  ...(producer?.artifact_refs ?? []),
                ].map((ref) => [`${ref.artifact_id}:${ref.version}`, ref]),
              ).values(),
            )}
            selectedArtifactId={selection.artifactId}
            version={selection.version}
            onRevoked={body.revoke}
            onSelectArtifact={(artifactId, version) =>
              onSelectionChange?.({
                ...selection,
                artifactId,
                version,
                citationId: null,
                panel: "artifact",
                stepId: null,
              })
            }
            onSelectProducer={({ runId, stepId }) =>
              onSelectionChange?.({
                ...selection,
                runId,
                stepId,
                artifactId: null,
                version: null,
                citationId: null,
                panel: "overview",
                at: runId === selection.runId ? selection.at : null,
              })
            }
            onSelectCitation={(ref) =>
              onSelectionChange?.({
                ...selection,
                citationId: ref.citation_id,
                artifactId: null,
                version: null,
                stepId: null,
                panel: "source",
              })
            }
            body={selection.artifactId ? content(artifactTarget, "Artifact") : undefined}
          />
          {selection.artifactId && !artifact && content(null, "Artifact")}
        </TabsContent>
        <TabsContent value="source" className="space-y-3">
          {!selection.citationId &&
            (detail?.citation_refs ?? []).map((ref) => (
              <Button
                key={ref.citation_id}
                variant="outline"
                onClick={() =>
                  onSelectionChange?.({
                    ...selection,
                    citationId: ref.citation_id,
                    artifactId: null,
                    version: null,
                    panel: "source",
                  })
                }
              >
                {ref.citation_id}
              </Button>
            ))}
          {selection.citationId &&
            (citation ? (
              <SourcePanel
                citation={citation}
                title={
                  sourceTarget ? body.items[sourceTarget.key]?.pages.at(-1)?.source_title : null
                }
                locator={locator}
                onLocatorChange={setLocator}
                body={content(sourceTarget, "Source")}
                producers={producer ? [{ runId: producer.run_id, stepId: producer.step_id }] : []}
                onSelectProducer={({ runId, stepId }) =>
                  onSelectionChange?.({
                    ...selection,
                    runId,
                    stepId,
                    citationId: null,
                    panel: "overview",
                  })
                }
              />
            ) : (
              content(null, "Source")
            ))}
        </TabsContent>
      </Tabs>
      {!detail && referenceTarget && producers === null && (
        <p role="status">{ownerError ? t("readError") : t("resolvingProducer")}</p>
      )}
      {!detail && producers && producers.length > 1 && (
        <label className="block space-y-1">
          {t("chooseProducer")}
          <select
            value={producerId ?? ""}
            onChange={(event) => setProducerId(event.target.value)}
            className="bg-background w-full rounded-md border p-2"
          >
            <option value="">{t("chooseProducer")}</option>
            {producers.map((value) => (
              <option key={value.step_id} value={value.step_id}>
                {value.step_id}
              </option>
            ))}
          </select>
        </label>
      )}
    </div>
  );
}
