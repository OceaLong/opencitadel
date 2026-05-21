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
  disabled?: boolean
  className?: string
}

export function SessionModelPicker({ value, onChange, disabled, className }: Props) {
  const [models, setModels] = useState<LLMModel[]>([])

  useEffect(() => {
    modelsApi.list().then((d) => setModels(d.models)).catch(() => {})
  }, [])

  const defaultModel = models.find((m) => m.is_default)

  const options = useMemo(() => {
    const supportedSet = new Set(SUPPORTED_PROVIDERS.map((p) => p.value))
    const list = models
      .filter((m) => supportedSet.has(m.provider))
      .map((m) => ({
        id: m.id,
        title: m.display_name,
        description: `${m.provider} · ${m.model_name}`,
        icon: <Cpu className="size-4 text-muted-foreground shrink-0" />,
        badge: m.is_default ? '默认' : undefined,
      }))

    if (defaultModel && supportedSet.has(defaultModel.provider)) {
      return [
        {
          id: 'default',
          title: `默认 (${defaultModel.display_name})`,
          description: '跟随全局默认模型',
          icon: <Cpu className="size-4 text-muted-foreground shrink-0" />,
          badge: '推荐',
        },
        ...list.filter((m) => m.id !== defaultModel.id),
      ]
    }

    return list
  }, [models, defaultModel])

  const pickerValue = value || 'default'

  return (
    <InlineOptionPicker
      value={pickerValue}
      options={options}
      placeholder="默认模型"
      onChange={(id) => onChange(id === 'default' ? undefined : id)}
      disabled={disabled}
      className={className}
    />
  )
}
