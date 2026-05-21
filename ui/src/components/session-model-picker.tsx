'use client'

import { useEffect, useMemo, useState } from 'react'
import { Cpu } from 'lucide-react'
import { modelsApi } from '@/lib/api/models'
import type { LLMModel } from '@/lib/api/types'
import { InlineOptionPicker } from '@/components/inline-option-picker'
import { SUPPORTED_PROVIDERS } from '@/components/settings/models-settings'

type Props = {
  value?: string | null
  onChange: (modelId: string | undefined) => void
  /** 模型列表加载完成后回调默认模型 id，便于父组件创建会话时带上默认模型 */
  onDefaultModelLoaded?: (modelId: string | undefined) => void
  disabled?: boolean
  className?: string
}

export function SessionModelPicker({
  value,
  onChange,
  onDefaultModelLoaded,
  disabled,
  className,
}: Props) {
  const [models, setModels] = useState<LLMModel[]>([])

  useEffect(() => {
    modelsApi.list().then((d) => setModels(d.models)).catch(() => {})
  }, [])

  const supportedModels = useMemo(() => {
    const supportedSet = new Set(SUPPORTED_PROVIDERS.map((p) => p.value))
    return models.filter((m) => supportedSet.has(m.provider))
  }, [models])

  const defaultModel = supportedModels.find((m) => m.is_default)

  useEffect(() => {
    onDefaultModelLoaded?.(defaultModel?.id)
  }, [defaultModel?.id, onDefaultModelLoaded])

  const options = useMemo(
    () =>
      supportedModels.map((m) => ({
        id: m.id,
        title: m.display_name,
        description: `${m.provider} · ${m.model_name}`,
        icon: <Cpu className="size-4 text-muted-foreground shrink-0" />,
        badge: m.is_default ? '默认' : undefined,
      })),
    [supportedModels]
  )

  const pickerValue = value ?? defaultModel?.id

  return (
    <InlineOptionPicker
      value={pickerValue}
      options={options}
      placeholder="暂无模型"
      onChange={onChange}
      disabled={disabled || options.length === 0}
      className={className}
    />
  )
}
