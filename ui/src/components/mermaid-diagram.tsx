"use client";

import { useEffect, useId, useState } from "react";
import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";
import { useTheme } from "@/providers/theme-provider";

let mermaidInitialized = false;
let mermaidTheme: "neutral" | "dark" | null = null;

export type MermaidDiagramProps = {
  chart: string;
  className?: string;
};

export function MermaidDiagram({ chart, className }: MermaidDiagramProps) {
  const t = useTranslations("mermaid");
  const { resolvedTheme } = useTheme();
  const id = useId().replace(/:/g, "");
  const [svg, setSvg] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const mermaidThemeName = resolvedTheme === "dark" ? "dark" : "neutral";

  useEffect(() => {
    let cancelled = false;
    const render = async () => {
      try {
        const mermaid = (await import("mermaid")).default;
        if (!mermaidInitialized || mermaidTheme !== mermaidThemeName) {
          mermaid.initialize({
            startOnLoad: false,
            theme: mermaidThemeName,
            securityLevel: "loose",
          });
          mermaidInitialized = true;
          mermaidTheme = mermaidThemeName;
        }
        const { svg: rendered } = await mermaid.render(`mmd-${id}`, chart);
        if (!cancelled) {
          setSvg(rendered);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : t("renderFailed"));
          setSvg("");
        }
      }
    };
    void render();
    return () => {
      cancelled = true;
    };
  }, [chart, id, mermaidThemeName, t]);

  if (error) {
    return (
      <pre className={cn("bg-muted overflow-x-auto rounded-lg p-3 text-xs", className)}>
        {chart}
      </pre>
    );
  }

  return (
    <div
      className={cn(
        "mermaid-diagram bg-card my-2 overflow-x-auto rounded-lg border p-3",
        className,
      )}
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
