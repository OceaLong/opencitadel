"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { BookOpen, FileText, Loader2, Plus, RefreshCw } from "lucide-react";
import { toast } from "sonner";

import { ChatInput } from "@/components/chat-input";
import { CheckpointRestoreDialog } from "@/components/checkpoint-restore-dialog";
import { AddDocumentDialog } from "@/components/knowledge/add-document-dialog";
import { CreateKBDialog } from "@/components/knowledge/create-kb-dialog";
import {
  formatIngestStreamError,
  isChatSendBlocked,
  parseKbDocHref,
} from "@/components/knowledge/knowledge-utils";
import { MermaidDiagram } from "@/components/mermaid-diagram";
import { SessionModeToggle } from "@/components/session-mode-toggle";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { VirtualizedTimeline } from "@/components/virtualized-timeline";

import { useSessionDetailView } from "@/hooks/use-session-detail-view";
import { useAuth } from "@/providers/auth-provider";
import { knowledgeApi } from "@/lib/api/knowledge";
import type { KnowledgeBase, KnowledgeDocument, SessionMode } from "@/lib/api/types";
import { cn } from "@/lib/utils";

type KnowledgeWorkspaceProps = { knowledgeBaseId?: string };

function graphForDocs(documents: KnowledgeDocument[]): string {
  if (!documents.length) return "graph TD\n  empty[暂无文档]";
  return `graph TD\n${documents
    .slice(0, 20)
    .map((doc, idx) => `  doc${idx}["${doc.title.replace(/["\[\]#]/g, "'")}"]`)
    .join("\n")}`;
}

export function KnowledgeWorkspace({ knowledgeBaseId }: KnowledgeWorkspaceProps) {
  const router = useRouter();
  const { user } = useAuth();
  const [items, setItems] = useState<KnowledgeBase[]>([]);
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [activeId, setActiveId] = useState(knowledgeBaseId ?? "");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [mode, setMode] = useState<SessionMode>("ask");
  const [createOpen, setCreateOpen] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [ingesting, setIngesting] = useState(false);
  const [ingestLog, setIngestLog] = useState<string[]>([]);
  const [sourceTitle, setSourceTitle] = useState("");
  const [sourceContent, setSourceContent] = useState("");
  const [listLoading, setListLoading] = useState(false);
  const [reindexing, setReindexing] = useState(false);
  const ingestCleanupRef = useRef<(() => void) | null>(null);
  const kbSwitchTokenRef = useRef(0);
  const sourceRequestRef = useRef(0);

  const view = useSessionDetailView({ sessionId: sessionId ?? "", mode });

  const loadList = useCallback(async () => {
    if (!user) {
      setItems([]);
      setListLoading(false);
      return;
    }
    setListLoading(true);
    try {
      const data = await knowledgeApi.list();
      setItems(data.knowledge_bases);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "加载知识库失败");
    } finally {
      setListLoading(false);
    }
  }, [user]);

  const loadDetail = useCallback(async (id: string, token: number) => {
    if (!user) return;
    try {
      const [kb, docs] = await Promise.all([knowledgeApi.get(id), knowledgeApi.listDocuments(id)]);
      if (token !== kbSwitchTokenRef.current) return;
      setItems((prev) => [kb, ...prev.filter((item) => item.id !== kb.id)]);
      setDocuments(docs.documents);
      const running = kb.status !== "ready" && kb.status !== "failed" && Boolean(kb.ingest_task_id);
      setIngesting(running);
      if (kb.status === "failed" && kb.error) {
        toast.error(kb.error);
      }
    } catch (err) {
      if (token !== kbSwitchTokenRef.current) return;
      toast.error(err instanceof Error ? err.message : "加载详情失败");
    }
  }, [user]);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  useEffect(() => {
    if (knowledgeBaseId) setActiveId(knowledgeBaseId);
  }, [knowledgeBaseId]);

  useEffect(() => {
    if (!activeId || !user) return;
    const token = ++kbSwitchTokenRef.current;
    setSessionId(null);
    setSourceContent("");
    setSourceTitle("");
    setIngestLog([]);
    router.replace(`/knowledge/${activeId}`);
    void loadDetail(activeId, token);
    void (async () => {
      try {
        const data = await knowledgeApi.createSession(activeId, { mode });
        if (token !== kbSwitchTokenRef.current) return;
        setSessionId(data.session_id);
      } catch (err) {
        if (token !== kbSwitchTokenRef.current) return;
        toast.error(err instanceof Error ? err.message : "创建会话失败");
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId, loadDetail, router, user]);

  useEffect(() => {
    if (!activeId || !ingesting || !user) return;
    ingestCleanupRef.current?.();
    ingestCleanupRef.current = knowledgeApi.ingestStream(
      activeId,
      (ev) => {
        const msg =
          ev.type === "step"
            ? `[${(ev.data as { name?: string }).name}] ${(ev.data as { description?: string }).description}`
            : ev.type === "message"
              ? ((ev.data as { message?: string }).message?.slice(0, 160) ?? "")
              : ev.type === "error"
                ? ((ev.data as { error?: string }).error ?? "索引失败")
                : ev.type;
        if (msg) setIngestLog((prev) => [...prev.slice(-20), msg]);
        if (ev.type === "error") {
          setIngesting(false);
          toast.error(formatIngestStreamError(ev.data));
          void loadDetail(activeId, kbSwitchTokenRef.current);
        }
        if (ev.type === "done") {
          setIngesting(false);
          void loadDetail(activeId, kbSwitchTokenRef.current);
        }
      },
      (error) => {
        setIngesting(false);
        toast.error(error.message || "索引流连接失败");
      },
    );
    return () => ingestCleanupRef.current?.();
  }, [activeId, ingesting, loadDetail, user]);

  const active = items.find((item) => item.id === activeId);
  const graph = useMemo(() => graphForDocs(documents), [documents]);
  const chatDisabled = isChatSendBlocked(sessionId, view.loading);

  const handleSourceClick = useCallback(
    async (value: string) => {
      const ref = parseKbDocHref(value);
      if (!ref || !activeId) return;
      const requestId = ++sourceRequestRef.current;
      try {
        const data = await knowledgeApi.readDocument(activeId, ref.docId, ref.page);
        if (requestId !== sourceRequestRef.current) return;
        setSourceTitle(`${data.document.title}${ref.page ? ` · p${ref.page}` : ""}`);
        setSourceContent(data.content || "暂无原文片段");
      } catch (err) {
        if (requestId !== sourceRequestRef.current) return;
        setSourceTitle("读取失败");
        setSourceContent(err instanceof Error ? err.message : "读取失败");
      }
    },
    [activeId],
  );

  const selectKb = (id: string) => {
    setActiveId(id);
    setDocuments([]);
    setSessionId(null);
    setSourceContent("");
    setSourceTitle("");
    setIngestLog([]);
  };

  const handleReindex = async () => {
    if (!activeId) return;
    setReindexing(true);
    try {
      await knowledgeApi.reindex(activeId);
      setIngesting(true);
      toast.success("已开始重新索引");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "重新索引失败");
    } finally {
      setReindexing(false);
    }
  };

  return (
    <div className="flex h-[calc(100vh-3.5rem)] overflow-hidden">
      <aside className="border-border bg-muted/20 flex w-72 shrink-0 flex-col border-r">
        <div className="border-border border-b px-3 py-2">
          <h2 className="text-sm font-medium">文档知识库</h2>
          <p className="text-muted-foreground truncate text-xs">{active ? active.name : "选择知识库开始问答"}</p>
        </div>
        <ScrollArea className="min-h-0 flex-1">
          <div className="p-2">
            <p className="text-muted-foreground mb-2 px-2 text-xs font-medium">知识库</p>
            {items.map((kb) => (
              <button
                key={kb.id}
                type="button"
                className={cn("hover:bg-muted mb-1 w-full rounded-lg px-2 py-1.5 text-left text-sm", activeId === kb.id && "bg-muted font-medium")}
                onClick={() => selectKb(kb.id)}
              >
                <div className="truncate">{kb.name}</div>
                <div className="text-muted-foreground text-xs">{kb.status} · {kb.doc_count ?? 0} 文档</div>
              </button>
            ))}
            {!items.length && !listLoading && <p className="text-muted-foreground px-2 py-3 text-sm">暂无知识库</p>}
            {listLoading && <p className="text-muted-foreground px-2 py-3 text-sm">加载中...</p>}
          </div>
          {activeId && (
            <div className="border-border border-t p-2">
              <p className="text-muted-foreground mb-2 px-2 text-xs font-medium">文档</p>
              {documents.map((doc) => (
                <button
                  key={doc.id}
                  type="button"
                  className="hover:bg-muted mb-1 flex w-full items-start gap-2 rounded px-2 py-1.5 text-left text-xs"
                  onClick={() => handleSourceClick(`kbdoc://${doc.id}`)}
                >
                  <FileText className="mt-0.5 size-3 shrink-0" />
                  <span className="min-w-0">
                    <span className="block truncate font-medium">{doc.title}</span>
                    <span className="text-muted-foreground">{doc.status}{doc.error ? ` · ${doc.error}` : ""}</span>
                  </span>
                </button>
              ))}
              {!documents.length && <p className="text-muted-foreground px-2 py-3 text-sm">暂无文档</p>}
            </div>
          )}
        </ScrollArea>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="border-border flex items-center justify-between border-b px-4 py-2">
          <div className="flex items-center gap-2">
            <BookOpen className="size-5" />
            <h1 className="text-sm font-semibold">企业文档知识库{active ? ` · ${active.name}` : ""}</h1>
          </div>
          <div className="flex items-center gap-2">
            <Button size="sm" variant="outline" onClick={() => setCreateOpen(true)}><Plus className="mr-1 size-4" />新建</Button>
            {activeId && <Button size="sm" variant="outline" onClick={() => setAddOpen(true)}><FileText className="mr-1 size-4" />添加文档</Button>}
            {activeId && (
              <Button size="sm" variant="outline" disabled={reindexing || ingesting} onClick={handleReindex}>
                {reindexing ? <Loader2 className="mr-1 size-4 animate-spin" /> : <RefreshCw className="mr-1 size-4" />}
                重新索引
              </Button>
            )}
          </div>
        </div>
        {active?.vector_degraded && (
          <div className="border-amber-500/40 bg-amber-500/10 text-amber-900 dark:text-amber-200 border-b px-4 py-2 text-xs">
            语义检索未启用或向量不可用，当前将以 BM25 检索为主。
          </div>
        )}
        {active?.status === "failed" && active.error && (
          <div className="border-destructive/40 bg-destructive/10 text-destructive border-b px-4 py-2 text-xs">
            {active.error}
          </div>
        )}
        <div className="flex min-h-0 flex-1">
          <main className="flex min-w-0 flex-1 flex-col">
            {!activeId ? (
              <div className="text-muted-foreground flex flex-1 flex-col items-center justify-center gap-4">
                <BookOpen className="size-12 opacity-40" />
                <p>选择或新建知识库开始问答</p>
                <Button onClick={() => setCreateOpen(true)}>新建知识库</Button>
              </div>
            ) : (
              <>
                {ingesting && (
                  <div className="bg-muted/50 border-border border-b px-4 py-2 text-xs">
                    <div className="flex items-center gap-2"><Loader2 className="size-3 animate-spin" />正在索引知识库...</div>
                    {ingestLog.slice(-3).map((line, idx) => <div key={idx} className="text-muted-foreground truncate">{line}</div>)}
                  </div>
                )}
                {(view.error || view.streamError) && (
                  <div className="border-destructive/40 bg-destructive/10 text-destructive border-b px-4 py-2 text-xs">
                    {view.error?.message || view.streamError?.message}
                  </div>
                )}
                <div ref={view.scrollContainerRef} className="flex-1 overflow-y-auto px-4 py-4">
                  <VirtualizedTimeline
                    timeline={view.timeline}
                    scrollContainerRef={view.scrollContainerRef}
                    sessionStatus={view.session?.status}
                    onViewAllFiles={view.handleViewAllFiles}
                    onFileClick={view.handleFileClick}
                    onToolClick={view.handleToolClick}
                    onClarifyAnswer={view.handleClarifyAnswer}
                    resolveCheckpoint={view.resolveCheckpoint}
                    onRestoreCheckpoint={view.handleRestoreCheckpoint}
                    restoringCheckpoint={view.restoringCheckpoint}
                    streaming={view.streaming}
                    onSourceClick={handleSourceClick}
                  />
                  {view.loading && <div className="text-muted-foreground py-2 text-sm">正在加载会话...</div>}
                  {view.streaming && <div className="text-muted-foreground py-2 text-sm">正在思考中...</div>}
                </div>
                <div className="border-border border-t p-4">
                  <ChatInput
                    sessionId={sessionId}
                    disabled={chatDisabled}
                    isRunning={view.streaming}
                    onSend={view.handleSend}
                    onStop={view.handleStop}
                    toolbarRight={<SessionModeToggle mode={mode} onChange={setMode} />}
                  />
                  {!sessionId && !view.loading && (
                    <p className="text-muted-foreground mt-2 text-xs">会话准备中，请稍候再提问</p>
                  )}
                </div>
              </>
            )}
          </main>
          <aside className="border-border flex w-96 shrink-0 flex-col border-l">
            <Tabs defaultValue="source" className="flex min-h-0 flex-1 flex-col">
              <TabsList className="mx-2 mt-2 grid w-auto grid-cols-2"><TabsTrigger value="source">来源</TabsTrigger><TabsTrigger value="graph">图谱</TabsTrigger></TabsList>
              <TabsContent value="source" className="min-h-0 flex-1 px-2 pb-2">
                <ScrollArea className="h-full">
                  {sourceContent ? <div className="p-2 text-xs leading-relaxed whitespace-pre-wrap"><div className="text-muted-foreground mb-2 font-medium">{sourceTitle}</div>{sourceContent}</div> : <p className="text-muted-foreground p-4 text-sm">点击回答中的文档引用定位原文</p>}
                </ScrollArea>
              </TabsContent>
              <TabsContent value="graph" className="min-h-0 flex-1 px-2 pb-2"><ScrollArea className="h-full"><MermaidDiagram chart={graph} /></ScrollArea></TabsContent>
            </Tabs>
          </aside>
        </div>
      </div>
      <CreateKBDialog open={createOpen} onOpenChange={setCreateOpen} onCreated={(kb) => { setItems((prev) => [kb, ...prev]); setActiveId(kb.id); }} />
      {activeId && (
        <AddDocumentDialog
          kbId={activeId}
          open={addOpen}
          onOpenChange={setAddOpen}
          onAdded={(kb) => {
            setItems((prev) => [kb, ...prev.filter((item) => item.id !== kb.id)]);
            setIngesting(true);
            void knowledgeApi.listDocuments(activeId).then((data) => setDocuments(data.documents));
          }}
        />
      )}
      <CheckpointRestoreDialog checkpoint={view.pendingCheckpoint} open={view.checkpointDialogOpen} restoring={view.restoringCheckpoint} onOpenChange={view.setCheckpointDialogOpen} onConfirm={view.confirmRestoreCheckpoint} />
    </div>
  );
}
