"use client";

import { useEffect, useId, useState } from "react";
import mermaid from "mermaid";

import { cn } from "@/lib/utils";

let mermaidInitialized = false;

function ensureMermaid() {
  if (!mermaidInitialized) {
    mermaid.initialize({
      startOnLoad: false,
      theme: "neutral",
      securityLevel: "loose",
    });
    mermaidInitialized = true;
  }
}

export type MermaidDiagramProps = {
  chart: string;
  className?: string;
};

export function MermaidDiagram({ chart, className }: MermaidDiagramProps) {
  const id = useId().replace(/:/g, "");
  const [svg, setSvg] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    ensureMermaid();
    const render = async () => {
      try {
        const { svg: rendered } = await mermaid.render(`mmd-${id}`, chart);
        if (!cancelled) {
          setSvg(rendered);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Mermaid 渲染失败");
          setSvg("");
        }
      }
    };
    void render();
    return () => {
      cancelled = true;
    };
  }, [chart, id]);

  if (error) {
    return (
      <pre className={cn("bg-muted overflow-x-auto rounded-lg p-3 text-xs", className)}>
        {chart}
      </pre>
    );
  }

  return (
    <div
      className={cn("mermaid-diagram my-2 overflow-x-auto rounded-lg bg-white p-3", className)}
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
