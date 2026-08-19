"use client";

import { useMemo } from "react";
import { useTranslations } from "next-intl";

import type { CodebaseArtifact } from "@/lib/api/types";

type EvidenceRef = {
  version_id?: string;
  path?: string;
  start_line?: number;
  end_line?: number;
  symbol_id?: string;
  analyzer?: string;
  confidence?: number;
};

type EvidenceEdge = {
  kind?: string;
  callee_name?: string;
  resolution?: string;
  confidence?: number;
  evidence_refs?: EvidenceRef[];
};

type CodeEvidencePanelProps = {
  artifact: CodebaseArtifact;
  onOpenSource: (path: string, line?: number) => void;
};

function evidenceEdges(artifact: CodebaseArtifact): EvidenceEdge[] {
  const raw = artifact.meta?.edges;
  return Array.isArray(raw) ? (raw as EvidenceEdge[]) : [];
}

export function CodeEvidencePanel({
  artifact,
  onOpenSource,
}: CodeEvidencePanelProps) {
  const t = useTranslations("codebase");
  const edges = useMemo(() => evidenceEdges(artifact), [artifact]);
  if (!edges.length) return null;

  return (
    <div className="mt-2 rounded border p-2 text-xs">
      <p className="text-muted-foreground mb-1 font-medium">{t("evidence")}</p>
      <ul className="space-y-1">
        {edges.flatMap((edge, edgeIndex) =>
          (edge.evidence_refs ?? []).map((ref, refIndex) => {
            if (!ref.path) return null;
            const start = ref.start_line || undefined;
            const lineRange =
              ref.end_line && ref.end_line !== ref.start_line
                ? `${ref.start_line}-${ref.end_line}`
                : `${ref.start_line ?? ""}`;
            return (
              <li key={`${edgeIndex}:${refIndex}:${ref.path}:${lineRange}`}>
                <button
                  type="button"
                  className="text-link hover:underline"
                  onClick={() => onOpenSource(ref.path!, start)}
                >
                  {ref.path}
                  {lineRange ? `:${lineRange}` : ""}
                </button>
                <span className="text-muted-foreground ml-1">
                  {[
                    edge.kind,
                    edge.resolution,
                    ref.analyzer,
                    typeof ref.confidence === "number"
                      ? ref.confidence.toFixed(2)
                      : undefined,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              </li>
            );
          }),
        )}
      </ul>
    </div>
  );
}
