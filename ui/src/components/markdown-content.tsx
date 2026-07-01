"use client";

import { useDeferredValue, useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { MermaidDiagram } from "@/components/mermaid-diagram";

import { cn } from "@/lib/utils";

export type MarkdownContentProps = {
  content: string;
  className?: string;
  onSourceClick?: (path: string, line?: number) => void;
};

/**
 * remark-gfm autolink 对紧跟 CJK 字符的 URL 边界检测不准确，
 * 会将 `https://example.com，后续中文` 整段识别为链接。
 * 在 URL 与相邻 CJK 字符/标点之间插入空格修正边界。
 */
const CJK_RANGES = "\u3000-\u303F\u4E00-\u9FFF\uFF01-\uFF60";
const URL_FOLLOWED_BY_CJK = new RegExp(`(https?:\\/\\/[^\\s${CJK_RANGES}]+)([${CJK_RANGES}])`, "g");

function normalizeAutolinks(text: string): string {
  return text.replace(URL_FOLLOWED_BY_CJK, "$1 $2");
}

const headingClasses: Record<string, string> = {
  h1: "text-lg font-semibold mt-4 mb-2 first:mt-0 text-foreground",
  h2: "text-base font-semibold mt-3 mb-1.5 first:mt-0 text-foreground",
  h3: "text-sm font-semibold mt-2.5 mb-1 first:mt-0 text-foreground",
  h4: "text-sm font-medium mt-2 mb-1 first:mt-0 text-foreground",
  h5: "text-sm font-medium mt-1.5 mb-0.5 first:mt-0 text-foreground",
  h6: "text-sm font-medium mt-1 mb-0.5 first:mt-0 text-foreground",
};

function buildComponents(
  onSourceClick?: (path: string, line?: number) => void,
): React.ComponentProps<typeof ReactMarkdown>["components"] {
  return {
  h1: ({ className, ...props }) => <h1 className={cn(headingClasses.h1, className)} {...props} />,
  h2: ({ className, ...props }) => <h2 className={cn(headingClasses.h2, className)} {...props} />,
  h3: ({ className, ...props }) => <h3 className={cn(headingClasses.h3, className)} {...props} />,
  h4: ({ className, ...props }) => <h4 className={cn(headingClasses.h4, className)} {...props} />,
  h5: ({ className, ...props }) => <h5 className={cn(headingClasses.h5, className)} {...props} />,
  h6: ({ className, ...props }) => <h6 className={cn(headingClasses.h6, className)} {...props} />,
  p: ({ className, ...props }) => (
    <p
      className={cn("text-foreground mb-2 text-sm leading-relaxed last:mb-0", className)}
      {...props}
    />
  ),
  ul: ({ className, ...props }) => (
    <ul
      className={cn("text-foreground mb-2 list-disc space-y-0.5 pl-5 text-sm", className)}
      {...props}
    />
  ),
  ol: ({ className, ...props }) => (
    <ol
      className={cn("text-foreground mb-2 list-decimal space-y-0.5 pl-5 text-sm", className)}
      {...props}
    />
  ),
  li: ({ className, ...props }) => <li className={cn("leading-relaxed", className)} {...props} />,
  strong: ({ className, ...props }) => (
    <strong className={cn("text-foreground font-semibold", className)} {...props} />
  ),
  code: ({ className, children, ...props }) => {
    const text = typeof children === "string" ? children : String(children ?? "");
    const lang = className?.replace("language-", "") ?? "";
    if (lang === "mermaid") {
      return <MermaidDiagram chart={text.trim()} />;
    }
    const isBlock = text.includes("\n") || lang.length > 0;
    return (
      <code
        className={cn(
          isBlock
            ? "bg-muted text-foreground my-2 block overflow-x-auto rounded-lg p-3 font-mono text-sm"
            : "bg-muted text-foreground inline rounded-md px-1.5 py-0.5 font-mono text-[0.8125em]",
          className,
        )}
        {...props}
      >
        {children}
      </code>
    );
  },
  pre: ({ className, ...props }) => (
    <pre className={cn("my-2 overflow-x-auto", className)} {...props} />
  ),
  blockquote: ({ className, ...props }) => (
    <blockquote
      className={cn(
        "border-border text-muted-foreground my-2 border-l-4 py-0.5 pl-3 text-sm italic",
        className,
      )}
      {...props}
    />
  ),
  a: ({ className, href, children, ...props }) => {
    const childText = String(children ?? "");
    if (href?.startsWith("kbdoc://") && onSourceClick) {
      return (
        <button
          type="button"
          className="text-sm text-blue-600 hover:underline"
          onClick={() => onSourceClick(href)}
        >
          {children}
        </button>
      );
    }
    const locMatch = childText.match(/^([^:]+):(\d+)$/);
    if (locMatch && onSourceClick) {
      return (
        <button
          type="button"
          className="text-sm text-blue-600 hover:underline"
          onClick={() => onSourceClick(locMatch[1], Number(locMatch[2]))}
        >
          {childText}
        </button>
      );
    }
    // 安全兜底：如果 href 包含 CJK 字符，说明 autolink 仍然误判，降级为纯文本
    if (href && /[\u4E00-\u9FFF\u3000-\u303F\uFF00-\uFFEF]/.test(href)) {
      return <span className="text-foreground text-sm">{children}</span>;
    }
    return (
      <a
        className={cn("text-sm text-blue-600 hover:underline", className)}
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        {...props}
      >
        {children}
      </a>
    );
  },
  };
}

export function MarkdownContent({ content, className, onSourceClick }: MarkdownContentProps) {
  const deferredContent = useDeferredValue(content);
  const normalized = useMemo(() => normalizeAutolinks(deferredContent), [deferredContent]);
  const components = useMemo(() => buildComponents(onSourceClick), [onSourceClick]);

  return (
    <div className={cn("markdown-content break-words", className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {normalized}
      </ReactMarkdown>
    </div>
  );
}
