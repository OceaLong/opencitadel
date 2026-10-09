"use client";
import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

import { fileApi } from "@/lib/api/file";
import type { components } from "@/lib/api/generated/schema";
import { knowledgeApi } from "@/lib/api/knowledge";
import type { KnowledgeBase, KnowledgeVersion } from "@/lib/api/types";

import { type EvaluationAccess, EvaluationError, useEvaluationTask } from "./evaluation-boundary";
import { Choice } from "./form-fields";
type Binding = components["schemas"]["KnowledgeBinding"];
export function ResourcePicker({
  access,
  attachments,
  bindings,
  onChange,
  disabled = false,
  hideAttachments = false,
}: {
  access: EvaluationAccess;
  attachments: string[];
  bindings: Binding[];
  onChange: (attachments: string[], bindings: Binding[]) => void;
  disabled?: boolean;
  hideAttachments?: boolean;
}) {
  const t = useTranslations("evaluations");
  const task = useEvaluationTask(access);
  const { run: runTask } = task;
  const currentSelection = useRef({ attachments, bindings, onChange });
  useEffect(() => {
    currentSelection.current = { attachments, bindings, onChange };
  }, [attachments, bindings, onChange]);
  const [bases, setBases] = useState<KnowledgeBase[]>([]);
  const [versions, setVersions] = useState<KnowledgeVersion[]>([]);
  const [base, setBase] = useState("");
  const [version, setVersion] = useState("");
  useEffect(() => {
    void runTask(
      (o) => knowledgeApi.list(100, 0, o),
      (v) => setBases(v.knowledge_bases),
    );
  }, [runTask]);
  if (task.error === "denied") return <EvaluationError error="denied" />;
  return (
    <fieldset disabled={disabled || task.pending} className="space-y-2 rounded-md border p-3">
      <legend className="px-1 text-sm font-medium">{t("resources")}</legend>
      <EvaluationError error={task.error} />
      {!hideAttachments && (
        <label className="block text-sm">
          {t("attachmentUpload")}
          <input
            type="file"
            className="block min-h-9 w-full text-sm"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f)
                void runTask(
                  (o) => fileApi.uploadFile({ file: f }, o),
                  (v) => {
                    const current = currentSelection.current;
                    current.onChange([...current.attachments, v.id], current.bindings);
                  },
                );
              e.target.value = "";
            }}
          />
        </label>
      )}
      <ul>
        {attachments.map((id) => (
          <li key={id} className="flex min-w-0 items-center gap-2 text-sm">
            <span className="truncate">{id}</span>
            <Button
              type="button"
              variant="ghost"
              onClick={() =>
                onChange(
                  attachments.filter((x) => x !== id),
                  bindings,
                )
              }
            >
              {t("remove")}
            </Button>
          </li>
        ))}
      </ul>
      <Choice
        label={t("knowledge")}
        value={base}
        onChange={(id) => {
          task.cancel();
          setBase(id);
          setVersion("");
          setVersions([]);
          if (id)
            void runTask(
              (o) => knowledgeApi.listVersions(id, o),
              (v) => setVersions(v.versions),
              true,
            );
        }}
      >
        <option value="">{t("select")}</option>
        {bases.map((b) => (
          <option key={b.id} value={b.id}>
            {b.name}
          </option>
        ))}
      </Choice>
      <Choice label={t("fixedVersion")} value={version} onChange={setVersion}>
        <option value="">{t("select")}</option>
        {versions
          .filter((v) => v.state === "ready")
          .map((v) => (
            <option key={v.id} value={v.id}>
              {v.id}
            </option>
          ))}
      </Choice>
      <Button
        type="button"
        variant="outline"
        disabled={!base || !version}
        onClick={() => {
          onChange(attachments, [
            ...bindings.filter((b) => b.resource_id !== base),
            { resource_id: base, version_id: version },
          ]);
          setVersion("");
        }}
      >
        {t("addResource")}
      </Button>
      <ul>
        {bindings.map((b) => (
          <li key={b.resource_id} className="flex min-w-0 items-center gap-2 text-sm">
            <span className="break-all">
              {bases.find((x) => x.id === b.resource_id)?.name ?? b.resource_id} · {b.version_id}
            </span>
            <Button
              type="button"
              variant="ghost"
              onClick={() =>
                onChange(
                  attachments,
                  bindings.filter((x) => x.resource_id !== b.resource_id),
                )
              }
            >
              {t("remove")}
            </Button>
          </li>
        ))}
      </ul>
    </fieldset>
  );
}
