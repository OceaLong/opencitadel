"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Cpu } from "lucide-react";

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
  const { user } = useAuth();
  const { promptLogin } = useLoginPrompt();
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
        badge: m.is_default ? "默认" : undefined,
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
        onClick={() => promptLogin("登录后即可选择模型")}
      >
        <Cpu className="text-muted-foreground size-4" />
        登录后选择模型
      </Button>
    );
  }

  return (
    <div className={className}>
      {options.length === 0 && (
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <Badge variant="destructive" className="text-[10px]">
            {llmUnavailable ? "模型暂不可用" : "未配置模型"}
          </Badge>
          <Link href="/settings" className="text-primary text-xs underline">
            前往模型设置
          </Link>
        </div>
      )}
      <InlineOptionPicker
        value={pickerValue}
        options={options}
        placeholder="暂无模型"
        onChange={onChange}
        disabled={disabled || options.length === 0}
      />
    </div>
  );
}
