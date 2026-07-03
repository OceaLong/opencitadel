"use client";

import { useEffect, useMemo, useState } from "react";
import { Cpu } from "lucide-react";
import { useTranslations } from "next-intl";

import { InlineOptionPicker } from "@/components/inline-option-picker";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

import {
  filterSupportedModels,
  loadModels,
  onModelsCacheInvalidated,
  resolveDefaultModelId,
} from "@/lib/api/models-cache";
import { isModelUnavailableStatus, llmStatusApi } from "@/lib/api/llm-status";
import type { LLMModel } from "@/lib/api/types";
import { useAuth } from "@/providers/auth-provider";
import { useLoginPrompt } from "@/providers/login-prompt-provider";
import { useSettingsDialog } from "@/providers/settings-dialog-provider";

type Props = {
  value?: string | null;
  onChange: (modelId: string | undefined) => void;
  /** 模型列表加载完成后回调默认模型 id，便于父组件创建会话时带上默认模型 */
  onDefaultModelLoaded?: (modelId: string | undefined) => void;
  /** 加载完成后回报是否存在受支持模型 */
  onModelsResolved?: (hasModels: boolean) => void;
  disabled?: boolean;
  className?: string;
};

export function SessionModelPicker({
  value,
  onChange,
  onDefaultModelLoaded,
  onModelsResolved,
  disabled,
  className,
}: Props) {
  const t = useTranslations("modelPicker");
  const tAuth = useTranslations("auth");
  const tCommon = useTranslations("common");
  const { user } = useAuth();
  const { promptLogin } = useLoginPrompt();
  const { openSettings } = useSettingsDialog();
  const [models, setModels] = useState<LLMModel[]>([]);
  const [llmUnavailable, setLlmUnavailable] = useState(false);

  useEffect(() => {
    let cancelled = false;
    llmStatusApi
      .getStatus()
      .then((data) => {
        if (!cancelled) setLlmUnavailable(isModelUnavailableStatus(data.status));
      })
      .catch(() => {
        if (!cancelled) setLlmUnavailable(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!user) {
      setModels([]);
      return;
    }

    let cancelled = false;

    const fetchModels = () => {
      loadModels()
        .then((items) => {
          if (!cancelled) setModels(items);
        })
        .catch(() => {
          if (!cancelled) setModels([]);
        });
    };

    fetchModels();
    const unsubscribe = onModelsCacheInvalidated(fetchModels);

    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [user]);

  const supportedModels = useMemo(() => filterSupportedModels(models), [models]);

  const defaultModelId = useMemo(
    () => resolveDefaultModelId(models),
    [models],
  );

  useEffect(() => {
    onDefaultModelLoaded?.(defaultModelId);
  }, [defaultModelId, onDefaultModelLoaded]);

  useEffect(() => {
    onModelsResolved?.(supportedModels.length > 0);
  }, [supportedModels.length, onModelsResolved]);

  const options = useMemo(
    () =>
      supportedModels.map((m) => ({
        id: m.id,
        title: m.display_name,
        description: `${m.provider} · ${m.model_name}`,
        icon: <Cpu className="text-muted-foreground size-4 shrink-0" />,
        badge: m.is_default ? tCommon("default") : undefined,
      })),
    [supportedModels],
  );

  const pickerValue = value ?? defaultModelId;

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
          <Badge variant="destructive" className="text-[10px]">
            {llmUnavailable ? t("unavailable") : t("notConfigured")}
          </Badge>
          <button
            type="button"
            className="text-primary text-xs underline"
            onClick={() => openSettings("models-setting")}
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
