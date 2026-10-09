"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";

import { EvaluationError, useEvaluationTask } from "@/components/evaluation/evaluation-boundary";
import { Button } from "@/components/ui/button";

import { analysisFailure } from "@/lib/analysis-view/failure";
import { analysisApi } from "@/lib/api/execution-analysis";
import type { ExportCreate, ExportJob } from "@/lib/api/types/execution-analysis";

import type { AnalysisAccess } from "./analysis-boundary";
type ExportPanelProps = {
  access: AnalysisAccess;
  source: ExportCreate;
  description: string;
};
export function ExportPanel(props: ExportPanelProps) {
  return <ExportOwner key={JSON.stringify([props.access.ownerKey, props.source])} {...props} />;
}
function ExportOwner({ access, source, description }: ExportPanelProps) {
  const t = useTranslations("analysis");
  const task = useEvaluationTask(access);
  const [failure, setFailure] = useState<ReturnType<typeof analysisFailure>>(null);
  const checked = useCallback(
    async <T,>(job: Promise<T>, signal?: AbortSignal | null) => {
      setFailure(null);
      try {
        return await job;
      } catch (error) {
        const kind = analysisFailure(error);
        if (!signal?.aborted) {
          setFailure(kind);
          if (kind === "expired") {
            intent.current = null;
            setJob(null);
          }
          if (kind === "sourceChanged") access.deny?.();
        }
        throw error;
      }
    },
    [access],
  );
  const [job, setJob] = useState<ExportJob | null>(null);
  const intent = useRef<string | null>(null);
  const url = useRef<string | null>(null);
  const revoke = () => {
    if (url.current) URL.revokeObjectURL(url.current);
    url.current = null;
  };
  useEffect(() => revoke, []);
  useEffect(() => {
    if (!job || !["queued", "running"].includes(job.status)) return;
    const timer = setTimeout(
      () => void task.run((o) => checked(analysisApi.getExport(job.id, o), o.signal), setJob),
      1500,
    );
    return () => clearTimeout(timer);
  }, [job, task, checked]);
  const create = () => {
    revoke();
    setJob(null);
    const payload = { ...source, request_id: (intent.current ??= crypto.randomUUID()) };
    void task.run(
      (o) => checked(analysisApi.createExport(payload, o), o.signal),
      (accepted) => {
        intent.current = null;
        setJob(accepted);
      },
    );
  };
  const download = () => {
    revoke();
    void task.run(
      async (o) => {
        const current = await checked(analysisApi.getExport(job!.id, o), o.signal);
        if (current.status !== "ready") {
          if (!o.signal?.aborted) setJob(current);
          throw new Error("export_unavailable");
        }
        return checked(analysisApi.downloadExport(current.id, o), o.signal);
      },
      (blob) => {
        url.current = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url.current;
        a.download = `execution-export-${job!.id}.${job!.format}`;
        a.click();
        revoke();
      },
    );
  };
  return (
    <section className="space-y-2">
      <p className="text-sm">{description}</p>
      <Button variant="outline" disabled={!access.canManage || task.pending} onClick={create}>
        {t(source.source_kind === "filter" ? "createExportSnapshot" : "exportRevision")} (
        {source.format.toUpperCase()})
      </Button>
      {failure === "expired" ? (
        <p role="alert">{t("exportExpired")}</p>
      ) : failure === "capacity" ? (
        <p role="alert">{t("exportCapacity")}</p>
      ) : failure === "quota" ? (
        <p role="alert">{t("exportQuota")}</p>
      ) : (
        <EvaluationError error={task.error} />
      )}
      {job && (
        <div role="status">
          <p>
            {t(`exportStatus.${job.status}`)} · {job.created_at ?? ""} · {t("expires")}:{" "}
            {job.expires_at ?? t("unknown")}
          </p>
          {["queued", "running"].includes(job.status) && (
            <progress aria-label={t("exportProgress")} />
          )}{" "}
          {job.status === "ready" && (
            <Button disabled={task.pending} onClick={download}>
              {t("download")}
            </Button>
          )}
        </div>
      )}
    </section>
  );
}
