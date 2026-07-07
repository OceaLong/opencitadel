"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronRight, FileCode2, Search } from "lucide-react";
import { useTranslations } from "next-intl";

import { MermaidDiagram } from "@/components/mermaid-diagram";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { codebaseApi } from "@/lib/api/codebase";
import type { CodebaseArtifact, CodebaseSymbol, FileTreeNode } from "@/lib/api/types";
import { cn } from "@/lib/utils";

const ARTIFACT_KINDS: CodebaseArtifact["kind"][] = [
  "architecture",
  "data_flow",
  "module_dir",
  "call_chain",
  "flowchart",
  "overview",
];

type CallChainLocation = {
  symbol: string;
  path?: string;
  line: number;
  symbol_id?: string;
};

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

type CodebaseContextPanelProps = {
  codebaseId: string;
  onSourceNavigate?: (path: string, line?: number) => void;
  onSourceClickRef?: React.MutableRefObject<((path: string, line?: number) => void) | null>;
};

export function CodebaseContextPanel({
  codebaseId,
  onSourceNavigate,
  onSourceClickRef,
}: CodebaseContextPanelProps) {
  const t = useTranslations("codebase");
  const tCommon = useTranslations("common");
  const tWorkspace = useTranslations("workspaceContext");
  const [tree, setTree] = useState<FileTreeNode[]>([]);
  const [artifacts, setArtifacts] = useState<CodebaseArtifact[]>([]);
  const [activeTab, setActiveTab] = useState("source");
  const [sourcePath, setSourcePath] = useState<string | null>(null);
  const [sourceContent, setSourceContent] = useState("");
  const [sourceLine, setSourceLine] = useState<number | undefined>();
  const [sourceLoading, setSourceLoading] = useState(false);
  const [symbolQuery, setSymbolQuery] = useState("");
  const [symbols, setSymbols] = useState<CodebaseSymbol[]>([]);
  const [symbolsLoading, setSymbolsLoading] = useState(false);

  useEffect(() => {
    if (!codebaseId) return;
    void (async () => {
      try {
        const [treeData, artData] = await Promise.all([
          codebaseApi.getTree(codebaseId),
          codebaseApi.getArtifacts(codebaseId),
        ]);
        setTree(treeData.tree);
        setArtifacts(artData.artifacts);
      } catch {
        setTree([]);
        setArtifacts([]);
      }
    })();
  }, [codebaseId]);

  useEffect(() => {
    if (!codebaseId) return;
    const query = symbolQuery.trim();
    if (query.length < 2) {
      setSymbols([]);
      return;
    }
    const timer = window.setTimeout(() => {
      setSymbolsLoading(true);
      void codebaseApi
        .listSymbols(codebaseId, query)
        .then((data) => setSymbols(data.symbols))
        .catch(() => setSymbols([]))
        .finally(() => setSymbolsLoading(false));
    }, 300);
    return () => window.clearTimeout(timer);
  }, [codebaseId, symbolQuery]);

  const loadSource = useCallback(
    async (path: string, line?: number) => {
      if (!codebaseId || !path) return;
      setActiveTab("source");
      setSourcePath(path);
      setSourceLine(line);
      setSourceLoading(true);
      onSourceNavigate?.(path, line);
      try {
        const data = await codebaseApi.readSource(codebaseId, {
          path,
          start_line: line ? Math.max(1, line - 5) : undefined,
          end_line: line ? line + 20 : undefined,
        });
        setSourceContent(data.content);
      } catch (err) {
        setSourceContent(err instanceof Error ? err.message : t("readFailed"));
      } finally {
        setSourceLoading(false);
      }
    },
    [codebaseId, onSourceNavigate, t],
  );

  useEffect(() => {
    if (onSourceClickRef) {
      onSourceClickRef.current = (path, line) => {
        void loadSource(path, line);
      };
    }
  }, [loadSource, onSourceClickRef]);

  const activeArtifact = (kind: CodebaseArtifact["kind"]) =>
    artifacts.find((a) => a.kind === kind);

  const callChainLocations = useMemo(() => {
    const art = artifacts.find((a) => a.kind === "call_chain");
    const locs = art?.meta?.node_locations;
    return Array.isArray(locs) ? (locs as CallChainLocation[]) : [];
  }, [artifacts]);

  const formatCallChainLabel = (loc: CallChainLocation) => {
    if (loc.path) {
      return `${loc.symbol} · ${loc.path}:${loc.line}`;
    }
    return `${loc.symbol}:${loc.line}`;
  };

  const artifactLabel = (kind: CodebaseArtifact["kind"]) => {
    const key = {
      architecture: "architecture",
      data_flow: "dataFlow",
      module_dir: "moduleDir",
      call_chain: "callChain",
      flowchart: "flowchart",
      overview: "overview",
    }[kind] as "architecture" | "dataFlow" | "moduleDir" | "callChain" | "flowchart" | "overview";
    return t(`artifacts.${key}`);
  };

  return (
    <aside className="flex h-full w-full flex-col">
      <div className="border-border border-b px-3 py-2">
        <p className="text-xs font-medium">{tWorkspace("codebasePanelTitle")}</p>
      </div>
      {tree.length > 0 && (
        <ScrollArea className="max-h-40 shrink-0 border-b">
          <div className="p-2">
            {tree.map((node) => (
              <FileTreeItem
                key={node.path}
                node={node}
                selectedPath={sourcePath}
                onSelect={(path) => void loadSource(path)}
              />
            ))}
          </div>
        </ScrollArea>
      )}
      <Tabs value={activeTab} onValueChange={setActiveTab} className="flex min-h-0 flex-1 flex-col">
        <TabsList className="mx-2 mt-2 grid w-auto grid-cols-3">
          <TabsTrigger value="source">{t("tabSource")}</TabsTrigger>
          <TabsTrigger value="symbols">{t("tabSymbols")}</TabsTrigger>
          <TabsTrigger value="diagrams">{t("tabDiagrams")}</TabsTrigger>
        </TabsList>
        <TabsContent value="source" className="min-h-0 flex-1 px-2 pb-2">
          <ScrollArea className="h-full">
            {sourcePath ? (
              <pre className="p-2 font-mono text-xs leading-relaxed whitespace-pre-wrap">
                {sourceLine && (
                  <span className="text-muted-foreground mb-2 block">
                    {sourcePath}:{sourceLine}
                  </span>
                )}
                {sourceLoading ? tCommon("loading") : sourceContent}
              </pre>
            ) : (
              <p className="text-muted-foreground p-4 text-sm">{t("sourceHint")}</p>
            )}
          </ScrollArea>
        </TabsContent>
        <TabsContent value="symbols" className="min-h-0 flex-1 px-2 pb-2">
          <div className="relative mb-2">
            <Search className="text-muted-foreground absolute top-2.5 left-2 size-3.5" />
            <Input
              value={symbolQuery}
              onChange={(e) => setSymbolQuery(e.target.value)}
              placeholder={t("symbolSearchPlaceholder")}
              className="h-8 pl-8 text-xs"
            />
          </div>
          <ScrollArea className="h-full">
            {symbolQuery.trim().length < 2 ? (
              <p className="text-muted-foreground p-4 text-sm">{t("symbolSearchHint")}</p>
            ) : symbolsLoading ? (
              <p className="text-muted-foreground p-4 text-sm">{tCommon("loading")}</p>
            ) : symbols.length === 0 ? (
              <p className="text-muted-foreground p-4 text-sm">{t("noSymbolsFound")}</p>
            ) : (
              <ul className="space-y-1 p-1">
                {symbols.map((symbol) => (
                  <li key={symbol.id}>
                    <button
                      type="button"
                      className="hover:bg-muted w-full rounded px-2 py-1.5 text-left text-xs"
                      onClick={() => {
                        if (symbol.path) {
                          void loadSource(symbol.path, symbol.start_line);
                        }
                      }}
                      disabled={!symbol.path}
                    >
                      <span className="font-medium">{symbol.name}</span>
                      <span className="text-muted-foreground ml-2">{symbol.kind}</span>
                      {symbol.path ? (
                        <span className="text-muted-foreground ml-1 block truncate">
                          {symbol.path}
                          {symbol.start_line ? `:${symbol.start_line}` : ""}
                        </span>
                      ) : null}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </ScrollArea>
        </TabsContent>
        <TabsContent value="diagrams" className="min-h-0 flex-1 px-2 pb-2">
          <ScrollArea className="h-full">
            {ARTIFACT_KINDS.map((kind) => {
              const art = activeArtifact(kind);
              if (!art) return null;
              const label = artifactLabel(kind);
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
                      {callChainLocations.map((loc) => {
                        const locKey = loc.path
                          ? `${loc.path}:${loc.line}`
                          : `${loc.symbol}:${loc.line}`;
                        const canNavigate = Boolean(loc.path);
                        return (
                          <li key={locKey}>
                            <button
                              type="button"
                              disabled={!canNavigate}
                              className={cn(
                                canNavigate
                                  ? "text-blue-600 hover:underline"
                                  : "text-muted-foreground cursor-not-allowed",
                              )}
                              onClick={() => {
                                if (loc.path) {
                                  void loadSource(loc.path, loc.line);
                                }
                              }}
                            >
                              {formatCallChainLabel(loc)}
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </div>
              );
            })}
          </ScrollArea>
        </TabsContent>
      </Tabs>
    </aside>
  );
}
