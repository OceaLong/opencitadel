"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";

import { knowledgeApi } from "@/lib/api/knowledge";
import type { KnowledgeDocumentContentItem } from "@/lib/api/types";
import { IconLoading } from "@/lib/icons";

type DocumentPagerProps = {
  knowledgeBaseId: string;
  versionId: string;
  documentId: string;
  page?: number;
  expectedRevisionId?: string;
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
}: DocumentPagerProps) {
  const identity = [
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
    />
  );
}

function BoundDocumentPager({
  knowledgeBaseId,
  versionId,
  documentId,
  page,
  expectedRevisionId,
}: DocumentPagerProps) {
  const t = useTranslations("knowledge");
  const [items, setItems] = useState<KnowledgeDocumentContentItem[]>([]);
  const [title, setTitle] = useState("");
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const requestGenerationRef = useRef(0);
  const inFlightRef = useRef(false);
  const pageErrorMessage = t("documentPageError");
  const revisionMismatchMessage = t("documentRevisionMismatch");

  useEffect(() => {
    const generation = requestGenerationRef.current + 1;
    requestGenerationRef.current = generation;
    inFlightRef.current = true;
    void knowledgeApi
      .readDocumentPage(knowledgeBaseId, versionId, documentId, {
        page,
        limit: 30,
      })
      .then((response) => {
        if (requestGenerationRef.current !== generation) return;
        if (expectedRevisionId && response.document_revision_id !== expectedRevisionId) {
          throw new Error(revisionMismatchMessage);
        }
        setTitle(response.document.title);
        setItems(response.items ?? []);
        setNextCursor(response.next_cursor ?? null);
      })
      .catch((reason: unknown) => {
        if (requestGenerationRef.current !== generation) return;
        setError(reason instanceof Error ? reason.message : pageErrorMessage);
      })
      .finally(() => {
        if (requestGenerationRef.current !== generation) return;
        inFlightRef.current = false;
        setLoading(false);
      });
    return () => {
      if (requestGenerationRef.current === generation) {
        requestGenerationRef.current += 1;
        inFlightRef.current = false;
      }
    };
  }, [
    documentId,
    expectedRevisionId,
    knowledgeBaseId,
    page,
    pageErrorMessage,
    revisionMismatchMessage,
    versionId,
  ]);

  const loadMore = useCallback(() => {
    const cursor = nextCursor;
    if (!cursor || inFlightRef.current) return;
    const generation = requestGenerationRef.current;
    inFlightRef.current = true;
    setLoading(true);
    setError("");
    void knowledgeApi
      .readDocumentPage(knowledgeBaseId, versionId, documentId, {
        cursor,
        limit: 30,
      })
      .then((response) => {
        if (requestGenerationRef.current !== generation) return;
        if (expectedRevisionId && response.document_revision_id !== expectedRevisionId) {
          throw new Error(revisionMismatchMessage);
        }
        setItems((current) => appendUniqueChunks(current, response.items ?? []));
        setNextCursor(response.next_cursor ?? null);
      })
      .catch((reason: unknown) => {
        if (requestGenerationRef.current !== generation) return;
        setError(reason instanceof Error ? reason.message : pageErrorMessage);
      })
      .finally(() => {
        if (requestGenerationRef.current !== generation) return;
        inFlightRef.current = false;
        setLoading(false);
      });
  }, [
    documentId,
    expectedRevisionId,
    knowledgeBaseId,
    nextCursor,
    pageErrorMessage,
    revisionMismatchMessage,
    versionId,
  ]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {title && <p className="px-2 py-1 text-xs font-medium">{title}</p>}
      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-3 p-2">
          {items.map((item) => (
            <section key={item.id} data-chunk-id={item.id}>
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
