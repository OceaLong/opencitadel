"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ChevronRight,
  Code2,
  Download,
  FileCode2,
  Loader2,
  Plus,
  RefreshCw,
} from "lucide-react";
import { toast } from "sonner";

import { ChatInput } from "@/components/chat-input";
import { VirtualizedTimeline } from "@/components/virtualized-timeline";
import { CreateCodebaseDialog } from "@/components/codebase/create-codebase-dialog";
import { MermaidDiagram } from "@/components/mermaid-diagram";
import { SessionModeToggle } from "@/components/session-mode-toggle";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { useSessionDetail } from "@/hooks/use-session-detail";
import { codebaseApi } from "@/lib/api/codebase";
import { sessionApi } from "@/lib/api/session";
import type {
  Codebase,
  CodebaseArtifact,
  FileTreeNode,
  SessionMode,
} from "@/lib/api/types";
import { eventsToTimeline } from "@/lib/session-events";
import { cn } from "@/lib/utils";

type CodebaseWorkspaceProps = {
  codebaseId?: string;
};

const ARTIFACT_TABS: { kind: CodebaseArtifact["kind"]; label: string }[] = [
  { kind: "architecture", label: "架构" },
  { kind: "data_flow", label: "数据流" },
  { kind: "module_dir", label: "目录" },
  { kind: "call_chain", label: "调用链" },
  { kind: "flowchart", label: "流程" },
  { kind: "overview", label: "概览" },
];

function FileTreeItem({
  node,
  selectedPath,
  onSelect,
  depth = 0,
}: {
  node: FileTreeNode;
  selectedPath: string | null;
  onSelect: (path: string) => void;
  depth?: number;
}) {
  const [open, setOpen] = useState(depth < 2);
  const isDir = node.is_dir || (node.children?.length ?? 0) > 0;
  if (isDir) {
    return (
      <div>
        <button
          type="button"
          className="hover:bg-muted flex w-full items-center gap-1 rounded px-2 py-1 text-left text-xs"
          style={{ paddingLeft: `${depth * 12 + 8}px` }}
          onClick={() => setOpen(!open)}
        >
          <ChevronRight className={cn("size-3 transition-transform", open && "rotate-90")} />
          <span className="truncate">{node.name}</span>
        </button>
        {open &&
          node.children?.map((child) => (
            <FileTreeItem
              key={child.path}
              node={child}
              selectedPath={selectedPath}
              onSelect={onSelect}
              depth={depth + 1}
            />
          ))}
      </div>
    );
  }
  return (
    <button
      type="button"
      className={cn(
        "hover:bg-muted flex w-full items-center gap-1 rounded px-2 py-1 text-left text-xs",
        selectedPath === node.path && "bg-muted font-medium",
      )}
      style={{ paddingLeft: `${depth * 12 + 20}px` }}
      onClick={() => onSelect(node.path)}
    >
      <FileCode2 className="size-3 shrink-0" />
      <span className="truncate">{node.name}</span>
    </button>
  );
}

export function CodebaseWorkspace({ codebaseId }: CodebaseWorkspaceProps) {
  const router = useRouter();
  const [codebases, setCodebases] = useState<Codebase[]>([]);
  const [activeId, setActiveId] = useState(codebaseId ?? "");
  const [tree, setTree] = useState<FileTreeNode[]>([]);
  const [artifacts, setArtifacts] = useState<CodebaseArtifact[]>([]);
  const [sourcePath, setSourcePath] = useState<string | null>(null);
  const [sourceContent, setSourceContent] = useState("");
  const [sourceLine, setSourceLine] = useState<number | undefined>();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [mode, setMode] = useState<SessionMode>("ask");
  const [createOpen, setCreateOpen] = useState(false);
  const [ingesting, setIngesting] = useState(false);
  const [ingestLog, setIngestLog] = useState<string[]>([]);
  const ingestCleanupRef = useRef<(() => void) | null>(null);

  const { events, streaming, sendMessage } = useSessionDetail(sessionId);

  const loadCodebases = useCallback(async () => {
    try {
      const data = await codebaseApi.list();
      setCodebases(data.codebases);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "加载代码库失败");
    }
  }, []);

  const loadCodebaseDetail = useCallback(async (id: string) => {
    try {
      const [cb, treeData, artData] = await Promise.all([
        codebaseApi.get(id),
        codebaseApi.getTree(id),
        codebaseApi.getArtifacts(id),
      ]);
      setTree(treeData.tree);
      setArtifacts(artData.artifacts);
      if (cb.status !== "ready" && cb.ingest_task_id) {
        setIngesting(true);
      } else {
        setIngesting(false);
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "加载详情失败");
    }
  }, []);

  useEffect(() => {
    void loadCodebases();
  }, [loadCodebases]);

  useEffect(() => {
    if (codebaseId) setActiveId(codebaseId);
  }, [codebaseId]);

  useEffect(() => {
    if (!activeId) return;
    router.replace(`/codebase/${activeId}`);
    void loadCodebaseDetail(activeId);
    void (async () => {
      try {
        const data = await codebaseApi.createSession(activeId, { mode });
        setSessionId(data.session_id);
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "创建会话失败");
      }
    })();
  }, [activeId, loadCodebaseDetail, router]);

  useEffect(() => {
    if (!activeId || !ingesting) return;
    ingestCleanupRef.current?.();
    ingestCleanupRef.current = codebaseApi.ingestStream(
      activeId,
      (ev) => {
        const msg =
          ev.type === "step"
            ? `[${(ev.data as { name?: string }).name}] ${(ev.data as { description?: string }).description}`
            : ev.type === "message"
              ? ((ev.data as { message?: string }).message?.slice(0, 120) ?? "")
              : ev.type;
        if (msg) setIngestLog((prev) => [...prev.slice(-20), msg]);
        if (ev.type === "done" || ev.type === "error") {
          setIngesting(false);
          void loadCodebaseDetail(activeId);
        }
      },
      () => setIngesting(false),
    );
    return () => ingestCleanupRef.current?.();
  }, [activeId, ingesting, loadCodebaseDetail]);

  const loadSource = useCallback(
    async (path: string, line?: number) => {
      if (!activeId || !path) return;
      setSourcePath(path);
      setSourceLine(line);
      try {
        const data = await codebaseApi.readSource(activeId, {
          path,
          start_line: line ? Math.max(1, line - 5) : undefined,
          end_line: line ? line + 20 : undefined,
        });
        setSourceContent(data.content);
      } catch (err) {
        setSourceContent(err instanceof Error ? err.message : "读取失败");
      }
    },
    [activeId],
  );

  const handleSend = useCallback(
    async (message: string, _files: unknown[]) => {
      if (!sessionId) return;
      await sendMessage(message, [], { mode });
    },
    [sessionId, sendMessage, mode],
  );

  const handleStop = useCallback(async () => {
    if (sessionId) await sessionApi.stopSession(sessionId);
  }, [sessionId]);

  const handleDownload = async () => {
    if (!activeId) return;
    try {
      const data = await codebaseApi.download(activeId);
      toast.success(`已打包: ${data.snapshot_key}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "下载失败");
    }
  };

  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const timeline = useMemo(() => eventsToTimeline(events), [events]);
  const handleSourceClick = useCallback(
    (path: string, line?: number) => {
      void loadSource(path, line);
    },
    [loadSource],
  );

  const activeArtifact = (kind: CodebaseArtifact["kind"]) =>
    artifacts.find((a) => a.kind === kind);

  const callChainLocations = useMemo(() => {
    const art = artifacts.find((a) => a.kind === "call_chain");
    const locs = art?.meta?.node_locations;
    return Array.isArray(locs) ? (locs as { symbol: string; line: number }[]) : [];
  }, [artifacts]);

  const activeCodebase = codebases.find((c) => c.id === activeId);

  return (
    <div className="flex h-[calc(100vh-3.5rem)] flex-col">
      <div className="border-border flex items-center justify-between border-b px-4 py-2">
        <div className="flex items-center gap-2">
          <Code2 className="size-5" />
          <h1 className="text-sm font-semibold">
            代码知识库{activeCodebase ? ` · ${activeCodebase.name}` : ""}
          </h1>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" onClick={() => setCreateOpen(true)}>
            <Plus className="mr-1 size-4" />
            新建
          </Button>
          {activeId && (
            <>
              <Button
                size="sm"
                variant="outline"
                onClick={async () => {
                  await codebaseApi.reanalyze(activeId);
                  setIngesting(true);
                }}
              >
                <RefreshCw className="mr-1 size-4" />
                重新分析
              </Button>
              <Button size="sm" variant="outline" onClick={handleDownload}>
                <Download className="mr-1 size-4" />
                下载
              </Button>
            </>
          )}
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        <aside className="border-border w-56 shrink-0 border-r">
          <ScrollArea className="h-full">
            <div className="p-2">
              <p className="text-muted-foreground mb-2 px-2 text-xs font-medium">代码库</p>
              {codebases.map((cb) => (
                <button
                  key={cb.id}
                  type="button"
                  className={cn(
                    "hover:bg-muted mb-1 w-full rounded-lg px-2 py-1.5 text-left text-sm",
                    activeId === cb.id && "bg-muted font-medium",
                  )}
                  onClick={() => setActiveId(cb.id)}
                >
                  <div className="truncate">{cb.name}</div>
                  <div className="text-muted-foreground text-xs">{cb.status}</div>
                </button>
              ))}
            </div>
            {activeId && tree.length > 0 && (
              <div className="border-border border-t p-2">
                <p className="text-muted-foreground mb-2 px-2 text-xs font-medium">文件目录</p>
                {tree.map((node) => (
                  <FileTreeItem
                    key={node.path}
                    node={node}
                    selectedPath={sourcePath}
                    onSelect={(p) => void loadSource(p)}
                  />
                ))}
              </div>
            )}
          </ScrollArea>
        </aside>

        <main className="flex min-w-0 flex-1 flex-col">
          {!activeId ? (
            <div className="text-muted-foreground flex flex-1 flex-col items-center justify-center gap-4">
              <Code2 className="size-12 opacity-40" />
              <p>选择或新建代码库开始分析</p>
              <Button onClick={() => setCreateOpen(true)}>新建代码库</Button>
            </div>
          ) : (
            <>
              {ingesting && (
                <div className="bg-muted/50 border-border border-b px-4 py-2 text-xs">
                  <div className="flex items-center gap-2">
                    <Loader2 className="size-3 animate-spin" />
                    正在分析代码库...
                  </div>
                  {ingestLog.slice(-3).map((l, i) => (
                    <div key={i} className="text-muted-foreground truncate">
                      {l}
                    </div>
                  ))}
                </div>
              )}
              <div ref={scrollContainerRef} className="flex-1 overflow-y-auto px-4 py-4">
                <VirtualizedTimeline
                  timeline={timeline}
                  scrollContainerRef={scrollContainerRef}
                  onViewAllFiles={() => {}}
                  onFileClick={() => {}}
                  onToolClick={() => {}}
                  onClarifyAnswer={() => {}}
                  resolveCheckpoint={() => undefined}
                  onRestoreCheckpoint={() => {}}
                  restoringCheckpoint={false}
                  streaming={streaming}
                  onSourceClick={handleSourceClick}
                />
                {streaming && (
                  <div className="text-muted-foreground py-2 text-sm">正在思考中...</div>
                )}
              </div>
              <div className="border-border border-t p-4">
                <ChatInput
                  sessionId={sessionId}
                  isRunning={streaming}
                  onSend={handleSend}
                  onStop={handleStop}
                  toolbarRight={<SessionModeToggle mode={mode} onChange={setMode} />}
                />
              </div>
            </>
          )}
        </main>

        <aside className="border-border w-96 shrink-0 border-l">
          <Tabs defaultValue="source" className="flex h-full flex-col">
            <TabsList className="mx-2 mt-2 grid w-auto grid-cols-2">
              <TabsTrigger value="source">源码</TabsTrigger>
              <TabsTrigger value="diagrams">图表</TabsTrigger>
            </TabsList>
            <TabsContent value="source" className="min-h-0 flex-1 px-2">
              <ScrollArea className="h-[calc(100vh-8rem)]">
                {sourcePath ? (
                  <pre className="p-2 font-mono text-xs leading-relaxed whitespace-pre-wrap">
                    {sourceLine && (
                      <span className="text-muted-foreground mb-2 block">
                        {sourcePath}:{sourceLine}
                      </span>
                    )}
                    {sourceContent}
                  </pre>
                ) : (
                  <p className="text-muted-foreground p-4 text-sm">点击文件或回答中的路径定位源码</p>
                )}
              </ScrollArea>
            </TabsContent>
            <TabsContent value="diagrams" className="min-h-0 flex-1 px-2">
              <ScrollArea className="h-[calc(100vh-8rem)]">
                {ARTIFACT_TABS.map(({ kind, label }) => {
                  const art = activeArtifact(kind);
                  if (!art) return null;
                  return (
                    <div key={kind} className="mb-4">
                      <h3 className="mb-2 text-sm font-medium">{art.title || label}</h3>
                      {art.format === "mermaid" ? (
                        <MermaidDiagram chart={art.content} />
                      ) : (
                        <pre className="text-xs whitespace-pre-wrap">{art.content}</pre>
                      )}
                      {kind === "call_chain" && callChainLocations.length > 0 && (
                        <ul className="mt-2 space-y-1 text-xs">
                          {callChainLocations.map((loc, i) => (
                            <li key={i}>
                              <button
                                type="button"
                                className="text-blue-600 hover:underline"
                                onClick={() => {
                                  const sym = loc.symbol;
                                  void loadSource(sym, loc.line);
                                }}
                              >
                                {loc.symbol}:{loc.line}
                              </button>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  );
                })}
              </ScrollArea>
            </TabsContent>
          </Tabs>
        </aside>
      </div>

      <CreateCodebaseDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={(cb) => {
          setCodebases((prev) => [cb, ...prev]);
          setActiveId(cb.id);
          setIngesting(true);
        }}
      />
    </div>
  );
}
