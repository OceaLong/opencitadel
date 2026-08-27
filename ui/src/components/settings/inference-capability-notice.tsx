"use client";

import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

import { useCapabilities } from "@/hooks/use-capabilities";
import { type CapabilityName, needsInferenceConfiguration } from "@/lib/api/capabilities";
import { useSettingsDialog } from "@/providers/settings-dialog-provider";

export function InferenceCapabilityNotice({
  capabilityName,
  enabled,
}: {
  capabilityName: Extract<CapabilityName, "embeddings" | "rerank">;
  enabled: boolean;
}) {
  const t = useTranslations("settingsInference");
  const { capability } = useCapabilities();
  const { openSettings } = useSettingsDialog();
  const state = capability(capabilityName);

  if (!enabled || !needsInferenceConfiguration(state)) return null;

  return (
    <div className="border-warning/30 bg-warning/5 flex flex-wrap items-center justify-between gap-3 rounded-lg border p-3 text-sm">
      <span>
        {capabilityName === "embeddings"
          ? t("embeddingCapabilityUnavailable")
          : t("rerankCapabilityUnavailable")}
      </span>
      <Button
        type="button"
        size="sm"
        variant="outline"
        onClick={() => openSettings("inference-setting")}
      >
        {t("configureInference")}
      </Button>
    </div>
  );
}
