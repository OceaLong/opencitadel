"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronRight, FileCode2 } from "lucide-react";
import { useTranslations } from "next-intl";

import { MermaidDiagram } from "@/components/mermaid-diagram";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { codebaseApi } from "@/lib/api/codebase";
import type { CodebaseArtifact, FileTreeNode } from "@/lib/api/types";
import { cn } from "@/lib/utils";

const ARTIFACT_KINDS: CodebaseArtifact["kind"][] = [
  "architecture",
  "data_flow",
  "module_dir",
  "call_chain",
  "flowchart",
  "overview",
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
  const tWorkspace = useTranslations("workspaceContext");
  const [tree, setTree] = useState<FileTreeNode[]>([]);
  const [artifacts, setArtifacts] = useState<CodebaseArtifact[]>([]);
  const [sourcePath, setSourcePath] = useState<string | null>(null);
  const [sourceContent, setSourceContent] = useState("");
  const [sourceLine, setSourceLine] = useState<number | undefined>();

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

  const loadSource = useCallback(
    async (path: string, line?: number) => {
      if (!codebaseId || !path) return;
      setSourcePath(path);
      setSourceLine(line);
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
    return Array.isArray(locs) ? (locs as { symbol: string; line: number }[]) : [];
  }, [artifacts]);

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
      <Tabs defaultValue="source" className="flex min-h-0 flex-1 flex-col">
        <TabsList className="mx-2 mt-2 grid w-auto grid-cols-2">
          <TabsTrigger value="source">{t("tabSource")}</TabsTrigger>
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
                {sourceContent}
              </pre>
            ) : (
              <p className="text-muted-foreground p-4 text-sm">{t("sourceHint")}</p>
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
                      {callChainLocations.map((loc, i) => (
                        <li key={i}>
                          <button
                            type="button"
                            className="text-blue-600 hover:underline"
                            onClick={() => void loadSource(loc.symbol, loc.line)}
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
  );
}
