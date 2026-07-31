"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";

import { knowledgeApi } from "@/lib/api/knowledge";
import type { KnowledgeGraphData } from "@/lib/api/types";
import { IconLoading } from "@/lib/icons";

type KnowledgeGraphProps = {
  knowledgeBaseId: string;
  versionId: string;
};

export function KnowledgeGraph({ knowledgeBaseId, versionId }: KnowledgeGraphProps) {
  return (
    <BoundKnowledgeGraph
      key={`${knowledgeBaseId}:${versionId}`}
      knowledgeBaseId={knowledgeBaseId}
      versionId={versionId}
    />
  );
}

function BoundKnowledgeGraph({ knowledgeBaseId, versionId }: KnowledgeGraphProps) {
  const t = useTranslations("knowledge");
  const [graph, setGraph] = useState<KnowledgeGraphData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const generationRef = useRef(0);
  const errorMessage = t("graphLoadError");

  useEffect(() => {
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    void knowledgeApi
      .getGraph(knowledgeBaseId, versionId, { limit: 100 })
      .then((response) => {
        if (generationRef.current === generation) setGraph(response);
      })
      .catch((reason: unknown) => {
        if (generationRef.current !== generation) return;
        setError(reason instanceof Error ? reason.message : errorMessage);
      })
      .finally(() => {
        if (generationRef.current === generation) setLoading(false);
      });
    return () => {
      if (generationRef.current === generation) {
        generationRef.current += 1;
      }
    };
  }, [errorMessage, knowledgeBaseId, versionId]);

  if (loading) {
    return (
      <div role="status" aria-label={t("graphLoading")} className="flex justify-center p-4">
        <IconLoading className="size-4 animate-spin" />
      </div>
    );
  }
  if (error) {
    return (
      <p role="alert" className="text-destructive p-4 text-sm">
        {error}
      </p>
    );
  }
  if (graph && !graph.capability) {
    return <p className="text-muted-foreground p-4 text-sm">{t("graphUnavailable")}</p>;
  }
  if (!graph || !graph.nodes.length) {
    return <p className="text-muted-foreground p-4 text-sm">{t("graphEmpty")}</p>;
  }
  const nodeNames = new Map(graph.nodes.map((node) => [node.id, node.name]));
  return (
    <div className="space-y-3">
      <ul className="grid gap-2 sm:grid-cols-2" aria-label={t("graphEntities")}>
        {graph.nodes.map((node) => (
          <li
            key={node.id}
            data-testid={`knowledge-node-${node.id}`}
            className="rounded border p-2 text-xs"
          >
            <p className="font-medium">{node.name}</p>
            {node.type && <p className="text-muted-foreground">{node.type}</p>}
            {node.description && <p>{node.description}</p>}
          </li>
        ))}
      </ul>
      <ul className="space-y-2" aria-label={t("graphRelations")}>
        {graph.edges.map((edge) => (
          <li
            key={edge.id}
            data-testid={`knowledge-edge-${edge.id}`}
            data-source={edge.source}
            data-target={edge.target}
            className="rounded border p-2 text-xs"
          >
            <p>
              {nodeNames.get(edge.source) ?? edge.source} {edge.relation}{" "}
              {nodeNames.get(edge.target) ?? edge.target}
            </p>
            {!!edge.evidence.length && (
              <ul aria-label={t("graphEvidence")}>
                {edge.evidence.map((citation) => (
                  <li
                    key={[
                      citation.version_id,
                      citation.document_revision_id,
                      citation.doc_id,
                      citation.chunk_id ?? "",
                    ].join(":")}
                    data-document={citation.doc_id}
                    data-revision={citation.document_revision_id}
                  >
                    {t("graphEvidenceItem", {
                      document: citation.doc_id,
                      revision: citation.document_revision_id,
                      page: citation.page_no ?? "-",
                    })}
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
