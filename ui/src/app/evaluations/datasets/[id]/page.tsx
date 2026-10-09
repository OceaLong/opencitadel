"use client";
import { use } from "react";

import { DatasetEditor } from "@/components/evaluation/dataset-editor";
import { EvaluationBoundary } from "@/components/evaluation/evaluation-boundary";
export default function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return (
    <EvaluationBoundary>
      {(access) => <DatasetEditor key={id} id={id} access={access} />}
    </EvaluationBoundary>
  );
}
