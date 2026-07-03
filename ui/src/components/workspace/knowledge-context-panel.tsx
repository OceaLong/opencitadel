"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";

import { MermaidDiagram } from "@/components/mermaid-diagram";
import { parseKbDocHref } from "@/components/knowledge/knowledge-utils";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { knowledgeApi } from "@/lib/api/knowledge";
import type { KnowledgeDocument } from "@/lib/api/types";
import { IconFilePreview } from "@/lib/icons";

function graphForDocs(documents: KnowledgeDocument[], emptyLabel: string): string {
  if (!documents.length) return `graph TD\n  empty[${emptyLabel.replace(/["\[\]#]/g, "'")}]`;
  return `graph TD\n${documents
    .slice(0, 20)
    .map((doc, idx) => `  doc${idx}["${doc.title.replace(/["\[\]#]/g, "'")}"]`)
    .join("\n")}`;
}

type KnowledgeContextPanelProps = {
  knowledgeBaseId: string;
  onSourceClickRef?: React.MutableRefObject<((value: string) => void) | null>;
};

export function KnowledgeContextPanel({
  knowledgeBaseId,
  onSourceClickRef,
}: KnowledgeContextPanelProps) {
  const t = useTranslations("knowledge");
  const tWorkspace = useTranslations("workspaceContext");
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [sourceTitle, setSourceTitle] = useState("");
  const [sourceContent, setSourceContent] = useState("");

  useEffect(() => {
    if (!knowledgeBaseId) return;
    void (async () => {
      try {
        const docs = await knowledgeApi.listDocuments(knowledgeBaseId);
        setDocuments(docs.documents);
      } catch {
        setDocuments([]);
      }
    })();
  }, [knowledgeBaseId]);

  const graph = useMemo(
    () => graphForDocs(documents, t("noDocumentsGraph")),
    [documents, t],
  );

  const loadDocument = useCallback(
    async (docId: string, page?: number) => {
      if (!knowledgeBaseId) return;
      try {
        const data = await knowledgeApi.readDocument(knowledgeBaseId, docId, page);
        setSourceTitle(`${data.document.title}${page ? ` · p${page}` : ""}`);
        setSourceContent(data.content || t("noSourceSnippet"));
      } catch (err) {
        setSourceTitle(t("readFailed"));
        setSourceContent(err instanceof Error ? err.message : t("readFailed"));
      }
    },
    [knowledgeBaseId, t],
  );

  const handleSourceClick = useCallback(
    (value: string) => {
      const ref = parseKbDocHref(value);
      if (!ref) return;
      void loadDocument(ref.docId, ref.page);
    },
    [loadDocument],
  );

  useEffect(() => {
    if (onSourceClickRef) {
      onSourceClickRef.current = handleSourceClick;
    }
  }, [handleSourceClick, onSourceClickRef]);

  return (
    <aside className="flex h-full w-full flex-col">
      <div className="border-border border-b px-3 py-2">
        <p className="text-xs font-medium">{tWorkspace("knowledgePanelTitle")}</p>
      </div>
      <ScrollArea className="max-h-40 shrink-0 border-b">
        <div className="p-2">
          {documents.map((doc) => (
            <button
              key={doc.id}
              type="button"
              className="hover:bg-muted mb-1 flex w-full items-start gap-2 rounded px-2 py-1.5 text-left text-xs"
              onClick={() => void loadDocument(doc.id)}
            >
              <IconFilePreview className="mt-0.5 size-3 shrink-0" />
              <span className="min-w-0">
                <span className="block truncate font-medium">{doc.title}</span>
                <span className="text-muted-foreground">{doc.status}</span>
              </span>
            </button>
          ))}
          {!documents.length && (
            <p className="text-muted-foreground px-2 py-3 text-sm">{t("noDocuments")}</p>
          )}
        </div>
      </ScrollArea>
      <Tabs defaultValue="source" className="flex min-h-0 flex-1 flex-col">
        <TabsList className="mx-2 mt-2 grid w-auto grid-cols-2">
          <TabsTrigger value="source">{tWorkspace("tabOriginalText")}</TabsTrigger>
          <TabsTrigger value="graph">{t("tabGraph")}</TabsTrigger>
        </TabsList>
        <TabsContent value="source" className="min-h-0 flex-1 px-2 pb-2">
          <ScrollArea className="h-full">
            {sourceTitle ? (
              <div className="p-2">
                <p className="mb-2 text-xs font-medium">{sourceTitle}</p>
                <pre className="font-mono text-xs leading-relaxed whitespace-pre-wrap">
                  {sourceContent}
                </pre>
              </div>
            ) : (
              <p className="text-muted-foreground p-4 text-sm">{tWorkspace("sourceHintKb")}</p>
            )}
          </ScrollArea>
        </TabsContent>
        <TabsContent value="graph" className="min-h-0 flex-1 px-2 pb-2">
          <ScrollArea className="h-full">
            <MermaidDiagram chart={graph} />
          </ScrollArea>
        </TabsContent>
      </Tabs>
    </aside>
  );
}
