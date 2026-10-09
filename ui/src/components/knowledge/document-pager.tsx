"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";

import { ApiError } from "@/lib/api/fetch";
import { knowledgeApi } from "@/lib/api/knowledge";
import type { KnowledgeDocumentContentItem } from "@/lib/api/types";
import { IconLoading } from "@/lib/icons";
import { useAuth } from "@/providers/auth-provider";
import { useClientDataScope } from "@/providers/client-data-provider";

type DocumentPagerProps = {
  knowledgeBaseId: string;
  versionId: string;
  documentId: string;
  page?: number;
  expectedRevisionId?: string;
  chunkId?: string;
  onRevoked?: () => void;
  workspaceId?: string;
};

function appendUniqueChunks(
  current: KnowledgeDocumentContentItem[],
  incoming: KnowledgeDocumentContentItem[],
): KnowledgeDocumentContentItem[] {
  const known = new Set(current.map((item) => item.id));
  return [
    ...current,
    ...incoming.filter((item) => {
      if (known.has(item.id)) return false;
      known.add(item.id);
      return true;
    }),
  ];
}

export function DocumentPager({
  knowledgeBaseId,
  versionId,
  documentId,
  page,
  expectedRevisionId,
  chunkId,
  onRevoked,
  workspaceId,
}: DocumentPagerProps) {
  const { user, loading } = useAuth();
  const { scope, scopeRevision } = useClientDataScope();
  const t = useTranslations("knowledge");
  if (loading || !user || scope?.userId !== user.id)
    return <p role="status">{t("documentPageError")}</p>;
  const identity = [
    user.id,
    scope.workspaceId,
    scopeRevision,
    chunkId,
    knowledgeBaseId,
    versionId,
    documentId,
    page ?? "",
    expectedRevisionId ?? "",
  ].join(":");
  return (
    <BoundDocumentPager
      key={identity}
      knowledgeBaseId={knowledgeBaseId}
      versionId={versionId}
      documentId={documentId}
      page={page}
      expectedRevisionId={expectedRevisionId}
      chunkId={chunkId}
      onRevoked={onRevoked}
      workspaceId={workspaceId ?? scope.workspaceId}
    />
  );
}

function BoundDocumentPager({
  knowledgeBaseId,
  versionId,
  documentId,
  page,
  expectedRevisionId,
  chunkId,
  onRevoked,
  workspaceId,
}: DocumentPagerProps) {
  const t = useTranslations("knowledge");
  const [items, setItems] = useState<KnowledgeDocumentContentItem[]>([]);
  const [title, setTitle] = useState("");
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const requestGenerationRef = useRef(0);
  const inFlightRef = useRef(false);
  const controllerRef = useRef<AbortController | null>(null);
  const denied = useRef(false);
  const pageErrorMessage = t("documentPageError");
  const revisionMismatchMessage = t("documentRevisionMismatch");
  const handleError = useCallback(
    (reason: unknown) => {
      if (
        reason instanceof ApiError &&
        ([401, 403, 404].includes(reason.code) ||
          (reason.data &&
            typeof reason.data === "object" &&
            "code" in reason.data &&
            reason.data.code === "resource_unavailable"))
      ) {
        denied.current = true;
        controllerRef.current?.abort();
        setItems([]);
        setTitle("");
        setNextCursor(null);
        onRevoked?.();
      }
      setError(reason instanceof Error ? reason.message : pageErrorMessage);
    },
    [onRevoked, pageErrorMessage],
  );

  useEffect(() => {
    const controller = new AbortController();
    controllerRef.current = controller;
    const generation = requestGenerationRef.current + 1;
    requestGenerationRef.current = generation;
    inFlightRef.current = true;
    void knowledgeApi
      .readDocumentPage(
        knowledgeBaseId,
        versionId,
        documentId,
        {
          page,
          limit: 30,
        },
        { workspaceId, signal: controllerRef.current?.signal, skipErrorHandler: true },
      )
      .then((response) => {
        if (requestGenerationRef.current !== generation) return;
        if (expectedRevisionId && response.document_revision_id !== expectedRevisionId) {
          throw new ApiError(409, revisionMismatchMessage, { code: "resource_unavailable" });
        }
        setTitle(response.document.title);
        setItems(response.items ?? []);
        setNextCursor(response.next_cursor ?? null);
      })
      .catch((reason: unknown) => {
        if (requestGenerationRef.current !== generation) return;
        handleError(reason);
      })
      .finally(() => {
        if (requestGenerationRef.current !== generation) return;
        inFlightRef.current = false;
        setLoading(false);
      });
    return () => {
      controller.abort();
      if (requestGenerationRef.current === generation) {
        requestGenerationRef.current += 1;
        inFlightRef.current = false;
      }
    };
  }, [
    documentId,
    handleError,
    workspaceId,
    expectedRevisionId,
    knowledgeBaseId,
    page,
    pageErrorMessage,
    revisionMismatchMessage,
    versionId,
  ]);

  const loadMore = useCallback(() => {
    const cursor = nextCursor;
    if (!cursor || inFlightRef.current || denied.current) return;
    const generation = requestGenerationRef.current;
    inFlightRef.current = true;
    setLoading(true);
    setError("");
    void knowledgeApi
      .readDocumentPage(
        knowledgeBaseId,
        versionId,
        documentId,
        {
          cursor,
          limit: 30,
        },
        { workspaceId, signal: controllerRef.current?.signal, skipErrorHandler: true },
      )
      .then((response) => {
        if (requestGenerationRef.current !== generation) return;
        if (expectedRevisionId && response.document_revision_id !== expectedRevisionId) {
          throw new ApiError(409, revisionMismatchMessage, { code: "resource_unavailable" });
        }
        setItems((current) => appendUniqueChunks(current, response.items ?? []));
        setNextCursor(response.next_cursor ?? null);
      })
      .catch((reason: unknown) => {
        if (requestGenerationRef.current !== generation) return;
        handleError(reason);
      })
      .finally(() => {
        if (requestGenerationRef.current !== generation) return;
        inFlightRef.current = false;
        setLoading(false);
      });
  }, [
    documentId,
    handleError,
    workspaceId,
    expectedRevisionId,
    knowledgeBaseId,
    nextCursor,
    revisionMismatchMessage,
    versionId,
  ]);

  useEffect(() => {
    let cancelled = false;
    if (chunkId && !loading && !error && nextCursor && !items.some((item) => item.id === chunkId))
      void Promise.resolve().then(() => {
        if (!cancelled) loadMore();
      });
    return () => {
      cancelled = true;
    };
  }, [chunkId, loading, error, nextCursor, items, loadMore]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {title && <p className="px-2 py-1 text-xs font-medium">{title}</p>}
      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-3 p-2">
          {chunkId &&
            !loading &&
            !nextCursor &&
            !items.some((item) => item.id === chunkId) &&
            !error && <p role="status">{t("documentChunkUnavailable")}</p>}
          {(chunkId && !items.some((item) => item.id === chunkId) ? [] : items).map((item) => (
            <section
              key={item.id}
              data-chunk-id={item.id}
              aria-current={item.id === chunkId ? "true" : undefined}
              className={item.id === chunkId ? "border-primary rounded border p-2" : undefined}
              ref={(node) => {
                if (node && item.id === chunkId) node.scrollIntoView?.({ block: "nearest" });
              }}
            >
              {item.heading_path && (
                <p className="text-muted-foreground mb-1 text-xs">{item.heading_path}</p>
              )}
              <pre className="font-mono text-xs leading-relaxed whitespace-pre-wrap">
                {item.content}
              </pre>
            </section>
          ))}
          {!loading && !items.length && !error && (
            <p className="text-muted-foreground text-sm">{t("documentPageEmpty")}</p>
          )}
        </div>
      </ScrollArea>
      {error && (
        <p role="alert" className="text-destructive px-2 py-1 text-xs">
          {error}
        </p>
      )}
      {(nextCursor || loading) && (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          disabled={loading || !nextCursor}
          aria-label={t("documentPageLoadMore")}
          onClick={loadMore}
        >
          {loading ? <IconLoading className="size-4 animate-spin" /> : t("documentPageLoadMore")}
        </Button>
      )}
    </div>
  );
}
