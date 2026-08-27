"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { Cpu } from "lucide-react";

import { InlineOptionPicker } from "@/components/session/inline-option-picker";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

import { useCapabilities } from "@/hooks/use-capabilities";
import {
  boundModelId,
  type InferenceSnapshot,
  loadInferenceSnapshot,
  modelsForPurpose,
} from "@/lib/api/inference-cache";
import { clientDataScopeKey } from "@/lib/data/client-data-scope";
import { useAuth } from "@/providers/auth-provider";
import { useClientDataScope } from "@/providers/client-data-provider";
import { useLoginPrompt } from "@/providers/login-prompt-provider";
import { useSettingsDialog } from "@/providers/settings-dialog-provider";

type Props = {
  value?: string | null;
  onChange: (modelId: string | undefined) => void;
  /** 模型列表加载完成后回调有效 chat binding 的 model id。 */
  onBoundModelLoaded?: (modelId: string | undefined) => void;
  /** 加载完成后回报是否存在受支持模型 */
  onModelsResolved?: (hasModels: boolean) => void;
  disabled?: boolean;
  className?: string;
};

export function SessionModelPicker({
  value,
  onChange,
  onBoundModelLoaded,
  onModelsResolved,
  disabled,
  className,
}: Props) {
  const t = useTranslations("modelPicker");
  const tAuth = useTranslations("auth");
  const tCommon = useTranslations("common");
  const { user } = useAuth();
  const { scope, scopeRevision, loadResource, resourceRevision } = useClientDataScope();
  const inferenceRevision = resourceRevision("inference");
  const { promptLogin } = useLoginPrompt();
  const { openSettings } = useSettingsDialog();
  const { capability } = useCapabilities();
  const scopeKey = scope ? clientDataScopeKey(scope) : null;
  const [loaded, setLoaded] = useState<{
    scopeKey: string;
    snapshot: InferenceSnapshot;
  } | null>(null);
  const snapshot = loaded?.scopeKey === scopeKey ? loaded.snapshot : null;

  useEffect(() => {
    if (!user || !scopeKey) return;

    let cancelled = false;
    const requestedScopeKey = scopeKey;

    void loadResource("inference", loadInferenceSnapshot)
      .then((value) => {
        if (!cancelled) setLoaded({ scopeKey: requestedScopeKey, snapshot: value });
      })
      .catch(() => {
        if (!cancelled) setLoaded(null);
      });

    return () => {
      cancelled = true;
    };
  }, [inferenceRevision, loadResource, scopeKey, scopeRevision, user]);

  const supportedModels = useMemo(
    () => modelsForPurpose(user ? (snapshot?.models ?? []) : [], "chat"),
    [snapshot, user],
  );

  const boundChatModelId = useMemo(
    () => boundModelId(user ? (snapshot?.bindings ?? []) : [], "chat"),
    [snapshot, user],
  );

  useEffect(() => {
    onBoundModelLoaded?.(boundChatModelId);
  }, [boundChatModelId, onBoundModelLoaded]);

  useEffect(() => {
    onModelsResolved?.(supportedModels.length > 0);
  }, [supportedModels.length, onModelsResolved]);

  const options = useMemo(
    () =>
      supportedModels.map((m) => ({
        id: m.id,
        title: m.display_name,
        description: `${snapshot?.endpoints.find((endpoint) => endpoint.id === m.endpoint_id)?.provider ?? "inference"} · ${m.model_name}`,
        icon: <Cpu className="text-muted-foreground size-4 shrink-0" />,
        badge: m.id === boundChatModelId ? tCommon("bound") : undefined,
      })),
    [boundChatModelId, snapshot, supportedModels, tCommon],
  );

  const pickerValue = value ?? boundChatModelId;

  if (!user) {
    return (
      <Button
        type="button"
        variant="outline"
        size="sm"
        className={className}
        disabled={disabled}
        onClick={() => promptLogin(tAuth("loginToSelectModel"))}
      >
        <Cpu className="text-muted-foreground size-4" />
        {tAuth("selectModelAfterLogin")}
      </Button>
    );
  }

  return (
    <div className={className}>
      {options.length === 0 && (
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <Badge variant="destructive" className="text-2xs">
            {capability("chat")?.state === "degraded" ? t("unavailable") : t("notConfigured")}
          </Badge>
          <button
            type="button"
            className="text-primary text-xs underline"
            onClick={() => openSettings("inference-setting")}
          >
            {t("goSettings")}
          </button>
        </div>
      )}
      <InlineOptionPicker
        value={pickerValue}
        options={options}
        placeholder={t("noModels")}
        onChange={onChange}
        disabled={disabled || options.length === 0}
      />
    </div>
  );
}
