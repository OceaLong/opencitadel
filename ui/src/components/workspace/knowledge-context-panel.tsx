"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";

import { DocumentPager } from "@/components/knowledge/document-pager";
import { KnowledgeGraph } from "@/components/knowledge/knowledge-graph";
import { parseKbDocHref } from "@/components/knowledge/knowledge-utils";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

type SelectedSource = {
  documentId: string;
  page?: number;
  revisionId?: string;
};

type KnowledgeContextPanelProps = {
  knowledgeBaseId: string;
  versionId: string;
  onSourceClickRef?: React.MutableRefObject<((value: string) => void) | null>;
};

export function KnowledgeContextPanel({
  knowledgeBaseId,
  versionId,
  onSourceClickRef,
}: KnowledgeContextPanelProps) {
  return (
    <BoundKnowledgeContextPanel
      key={`${knowledgeBaseId}:${versionId}`}
      knowledgeBaseId={knowledgeBaseId}
      versionId={versionId}
      onSourceClickRef={onSourceClickRef}
    />
  );
}

function BoundKnowledgeContextPanel({
  knowledgeBaseId,
  versionId,
  onSourceClickRef,
}: KnowledgeContextPanelProps) {
  const t = useTranslations("knowledge");
  const tWorkspace = useTranslations("workspaceContext");
  const [selectedSource, setSelectedSource] = useState<SelectedSource | null>(null);
  const [citationError, setCitationError] = useState("");

  const handleSourceClick = useCallback(
    (value: string) => {
      const reference = parseKbDocHref(value);
      if (!reference) return;
      if (reference.versionId && reference.versionId !== versionId) {
        setCitationError(t("citationVersionMismatch"));
        return;
      }
      setCitationError("");
      setSelectedSource({
        documentId: reference.docId,
        page: reference.page,
        revisionId: reference.revisionId,
      });
    },
    [t, versionId],
  );

  useEffect(() => {
    if (!onSourceClickRef) return;
    onSourceClickRef.current = handleSourceClick;
    return () => {
      if (onSourceClickRef.current === handleSourceClick) {
        onSourceClickRef.current = null;
      }
    };
  }, [handleSourceClick, onSourceClickRef]);

  return (
    <aside className="flex h-full w-full flex-col">
      <div className="border-border border-b px-3 py-2">
        <p className="text-xs font-medium">{tWorkspace("knowledgePanelTitle")}</p>
        <p className="text-muted-foreground truncate text-xs">
          {t("boundVersion", { version: versionId })}
        </p>
      </div>
      <Tabs defaultValue="source" className="flex min-h-0 flex-1 flex-col">
        <TabsList className="mx-2 mt-2 grid w-auto grid-cols-2">
          <TabsTrigger value="source">{tWorkspace("tabOriginalText")}</TabsTrigger>
          <TabsTrigger value="graph">{t("tabGraph")}</TabsTrigger>
        </TabsList>
        <TabsContent value="source" className="flex min-h-0 flex-1 flex-col px-2 pb-2">
          {citationError && (
            <p role="alert" className="text-destructive p-2 text-sm">
              {citationError}
            </p>
          )}
          {selectedSource ? (
            <DocumentPager
              knowledgeBaseId={knowledgeBaseId}
              versionId={versionId}
              documentId={selectedSource.documentId}
              page={selectedSource.page}
              expectedRevisionId={selectedSource.revisionId}
            />
          ) : (
            <p className="text-muted-foreground p-4 text-sm">{tWorkspace("sourceHintKb")}</p>
          )}
        </TabsContent>
        <TabsContent value="graph" className="min-h-0 flex-1 overflow-auto px-2 pb-2">
          <KnowledgeGraph knowledgeBaseId={knowledgeBaseId} versionId={versionId} />
        </TabsContent>
      </Tabs>
    </aside>
  );
}
