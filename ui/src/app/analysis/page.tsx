"use client";
import { AnalysisBoundary } from "@/components/analysis/analysis-boundary";
import { AnalysisPage } from "@/components/analysis/analysis-page";
export default function Page() {
  return <AnalysisBoundary>{(access) => <AnalysisPage access={access} />}</AnalysisBoundary>;
}
