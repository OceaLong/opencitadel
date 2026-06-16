"use client";

import { useEffect, useMemo, useState } from "react";
import { Cpu } from "lucide-react";

import { InlineOptionPicker } from "@/components/inline-option-picker";

import {
  filterSupportedModels,
  loadModels,
  onModelsCacheInvalidated,
  resolveDefaultModelId,
} from "@/lib/api/models-cache";
import type { LLMModel } from "@/lib/api/types";

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
  const [models, setModels] = useState<LLMModel[]>([]);

  useEffect(() => {
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
  }, []);

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

  return (
    <InlineOptionPicker
      value={pickerValue}
      options={options}
      placeholder="暂无模型"
      onChange={onChange}
      disabled={disabled || options.length === 0}
      className={className}
    />
  );
}
