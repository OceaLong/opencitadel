"use client";

import { useMemo } from "react";
import { Globe, Play, Sparkles } from "lucide-react";
import { useTranslations } from "next-intl";

import { MarkdownContent } from "@/components/markdown-content";
import type { ToolKind } from "@/components/tool-use/utils";
import { getArg } from "@/components/tool-use/utils";
import { ScrollArea } from "@/components/ui/scroll-area";

import type { ToolEvent } from "@/lib/api/types";

type ConsoleRecord = { ps1: string; command: string; output: string };

type SearchResultItem = { url: string; title: string; snippet: string };

function getToolContent(tool: ToolEvent): Record<string, unknown> | null {
  const content = tool.content;
  if (content && typeof content === "object" && !Array.isArray(content)) {
    return content as Record<string, unknown>;
  }
  return null;
}

export function JumpToLatestButton({ onClick }: { onClick: () => void }) {
  const t = useTranslations("toolPreview");

  return (
    <button
      type="button"
      onClick={onClick}
      className="bg-card/90 text-foreground hover:bg-card border-border/70 inline-flex cursor-pointer items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm shadow-[var(--shadow-card)] backdrop-blur transition-colors"
    >
      <Play size={12} className="fill-current" />
      <span>{t("jumpToLive")}</span>
    </button>
  );
}

function ShellPreview({ tool }: { tool: ToolEvent }) {
  const t = useTranslations("toolPreview");
  const content = getToolContent(tool);
  const consoleData = content?.console;
  const sessionId = getArg(tool.args, "session_id");

  const records: ConsoleRecord[] = useMemo(() => {
    if (Array.isArray(consoleData)) return consoleData as ConsoleRecord[];
    return [];
  }, [consoleData]);

  return (
    <div className="flex h-full flex-col gap-3 p-4">
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-gray-700 bg-[#1e1e1e]">
        <div className="flex-shrink-0 border-b border-gray-700 bg-[#2d2d2d] py-1.5 text-center text-xs text-gray-400">
          {sessionId || "shell"}
        </div>
        <ScrollArea className="flex-1">
          <div className="p-4 font-mono text-sm leading-relaxed">
            {records.length > 0 ? (
              records.map((rec, i) => (
                <div key={i} className="mb-2">
                  <div>
                    <span className="text-green-400">{rec.ps1}</span>{" "}
                    <span className="text-white">{rec.command}</span>
                  </div>
                  {rec.output && (
                    <pre className="mt-0.5 break-words whitespace-pre-wrap text-gray-300">
                      {rec.output}
                    </pre>
                  )}
                </div>
              ))
            ) : (
              <span className="text-gray-500">{t("waitingShellOutput")}</span>
            )}
          </div>
        </ScrollArea>
      </div>
    </div>
  );
}

function BrowserPreview({ tool, onOpenVNC }: { tool: ToolEvent; onOpenVNC?: () => void }) {
  const t = useTranslations("toolPreview");
  const content = getToolContent(tool);
  const screenshot = typeof content?.screenshot === "string" ? content.screenshot : null;
  const url = getArg(tool.args, "url", "href", "link");

  return (
    <div className="flex h-full flex-col gap-3 p-4">
      {url && (
        <div className="bg-muted/60 border-border/70 text-muted-foreground flex flex-shrink-0 items-center gap-2 rounded-lg border px-3 py-2 text-sm">
          <Globe size={14} className="text-muted-foreground flex-shrink-0" />
          <span className="truncate">{url}</span>
        </div>
      )}
      <div className="relative min-h-0 flex-1 overflow-hidden rounded-lg border">
        {screenshot ? (
          <ScrollArea className="h-full">
            <img src={screenshot} alt={t("browserScreenshot")} className="h-auto w-full" />
          </ScrollArea>
        ) : (
          <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
            {t("waitingScreenshot")}
          </div>
        )}
        {onOpenVNC && (
          <button
            type="button"
            onClick={onOpenVNC}
            className="absolute right-3 bottom-3 z-10 flex h-9 w-9 cursor-pointer items-center justify-center rounded-full bg-gray-800/80 text-white shadow-lg transition-colors hover:bg-gray-700"
            aria-label={t("openRemoteDesktop")}
          >
            <Sparkles size={16} />
          </button>
        )}
      </div>
    </div>
  );
}

function SearchPreview({ tool }: { tool: ToolEvent }) {
  const t = useTranslations("toolPreview");
  const content = getToolContent(tool);
  const rawResults = content?.results;

  const results: SearchResultItem[] = useMemo(() => {
    if (Array.isArray(rawResults)) return rawResults as SearchResultItem[];
    return [];
  }, [rawResults]);

  const query = getArg(tool.args, "query", "q");

  return (
    <ScrollArea className="h-full">
      <div className="flex flex-col gap-1 p-4">
        {query && (
          <div className="text-muted-foreground mb-3 text-sm">
            {t("searchResults", { query, count: results.length })}
          </div>
        )}
        {results.length > 0 ? (
          results.map((item, i) => (
            <a
              key={i}
              href={item.url}
              target="_blank"
              rel="noopener noreferrer"
              className="hover:bg-muted/60 group block rounded-lg p-3 transition-colors"
            >
              <div className="mb-0.5 truncate text-xs text-green-700">{item.url}</div>
              <div className="mb-1 line-clamp-1 text-sm font-medium text-blue-700 group-hover:underline">
                {item.title}
              </div>
              {item.snippet && (
                <div className="text-muted-foreground line-clamp-2 text-xs">{item.snippet}</div>
              )}
            </a>
          ))
        ) : (
          <div className="text-muted-foreground py-8 text-center text-sm">{t("noSearchResults")}</div>
        )}
      </div>
    </ScrollArea>
  );
}

function isMarkdownPath(filepath: string | null | undefined): boolean {
  if (!filepath) return false;
  const ext = filepath.toLowerCase().split(".").pop();
  return ext === "md" || ext === "markdown";
}

function FileToolPreview({ tool }: { tool: ToolEvent }) {
  const t = useTranslations("toolPreview");
  const content = getToolContent(tool);
  const fileContent = typeof content?.content === "string" ? content.content : null;
  const filepath = getArg(tool.args, "filepath", "path", "pathname");
  const isMarkdown = isMarkdownPath(filepath);

  return (
    <div className="flex h-full flex-col gap-3 p-4">
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-gray-700 bg-[#1e1e1e]">
        {filepath && (
          <div className="flex-shrink-0 truncate border-b border-gray-700 bg-[#2d2d2d] px-4 py-1.5 text-center text-xs text-gray-400">
            {filepath}
          </div>
        )}
        <ScrollArea className="flex-1">
          {isMarkdown && fileContent ? (
            <div className="bg-card p-4">
              <MarkdownContent content={fileContent} />
            </div>
          ) : (
            <pre className="p-4 font-mono text-sm leading-relaxed break-words whitespace-pre-wrap text-gray-300">
              {fileContent ?? t("waitingFileContent")}
            </pre>
          )}
        </ScrollArea>
      </div>
    </div>
  );
}

function ResultBlock({ value, fallback }: { value: unknown; fallback: string }) {
  return (
    <div className="rounded-lg border border-gray-700 bg-[#1e1e1e] p-4">
      <pre className="font-mono text-sm break-words whitespace-pre-wrap text-gray-300">
        {value != null
          ? typeof value === "string"
            ? value
            : JSON.stringify(value, null, 2)
          : fallback}
      </pre>
    </div>
  );
}

function MCPPreview({ tool }: { tool: ToolEvent }) {
  const t = useTranslations("toolPreview");
  const content = getToolContent(tool);
  const result = content?.result;

  return (
    <ScrollArea className="h-full">
      <div className="flex flex-col gap-4 p-4">
        <div className="flex flex-col gap-1">
          <div className="text-muted-foreground text-xs tracking-wide uppercase">{t("toolInfo")}</div>
          <div className="border-border/70 bg-muted/40 rounded-lg border p-3 text-sm">
            <div>
              <span className="text-muted-foreground">{t("nameLabel")}</span>
              <span className="text-foreground">{tool.name}</span>
            </div>
            <div>
              <span className="text-muted-foreground">{t("functionLabel")}</span>
              <span className="text-foreground">{tool.function}</span>
            </div>
            {Object.keys(tool.args).length > 0 && (
              <div className="mt-1">
                <span className="text-muted-foreground">{t("argsLabel")}</span>
                <pre className="text-foreground mt-1 text-xs break-words whitespace-pre-wrap">
                  {JSON.stringify(tool.args, null, 2)}
                </pre>
              </div>
            )}
          </div>
        </div>
        <div className="flex flex-col gap-1">
          <div className="text-muted-foreground text-xs tracking-wide uppercase">{t("result")}</div>
          <ResultBlock value={result} fallback={t("waitingResult")} />
        </div>
      </div>
    </ScrollArea>
  );
}

function A2APreview({ tool }: { tool: ToolEvent }) {
  const t = useTranslations("toolPreview");
  const content = getToolContent(tool);
  const result = content?.a2a_result;
  const query = getArg(tool.args, "query", "message", "input");

  return (
    <ScrollArea className="h-full">
      <div className="flex flex-col gap-4 p-4">
        <div className="flex flex-col gap-1">
          <div className="text-muted-foreground text-xs tracking-wide uppercase">
            {t("agentCallInfo")}
          </div>
          <div className="border-border/70 bg-muted/40 rounded-lg border p-3 text-sm">
            <div>
              <span className="text-muted-foreground">{t("toolLabel")}</span>
              <span className="text-foreground">{tool.name}</span>
            </div>
            <div>
              <span className="text-muted-foreground">{t("functionLabel")}</span>
              <span className="text-foreground">{tool.function}</span>
            </div>
            {query && (
              <div>
                <span className="text-muted-foreground">{t("instructionLabel")}</span>
                <span className="text-foreground">{query}</span>
              </div>
            )}
          </div>
        </div>
        <div className="flex flex-col gap-1">
          <div className="text-muted-foreground text-xs tracking-wide uppercase">{t("result")}</div>
          <ResultBlock value={result} fallback={t("waitingResult")} />
        </div>
      </div>
    </ScrollArea>
  );
}

function DefaultPreview({ tool }: { tool: ToolEvent }) {
  const t = useTranslations("toolPreview");

  return (
    <ScrollArea className="h-full">
      <div className="flex flex-col gap-4 p-4">
        <div className="border-border/70 bg-muted/40 rounded-lg border p-3 text-sm">
          <div>
            <span className="text-muted-foreground">{t("nameLabel")}</span>
            <span className="text-foreground">{tool.name}</span>
          </div>
          <div>
            <span className="text-muted-foreground">{t("functionLabel")}</span>
            <span className="text-foreground">{tool.function}</span>
          </div>
        </div>
        {tool.content != null && <ResultBlock value={tool.content} fallback="" />}
      </div>
    </ScrollArea>
  );
}

export function ToolPreviewContent({
  kind,
  tool,
  onOpenVNC,
}: {
  kind: ToolKind;
  tool: ToolEvent;
  onOpenVNC?: () => void;
}) {
  if (kind === "bash") return <ShellPreview tool={tool} />;
  if (kind === "browser") return <BrowserPreview tool={tool} onOpenVNC={onOpenVNC} />;
  if (kind === "search") return <SearchPreview tool={tool} />;
  if (kind === "file") return <FileToolPreview tool={tool} />;
  if (kind === "mcp") return <MCPPreview tool={tool} />;
  if (kind === "a2a") return <A2APreview tool={tool} />;
  return <DefaultPreview tool={tool} />;
}
