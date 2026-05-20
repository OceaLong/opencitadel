'use client'

import { useEffect, useState } from 'react'
import { modelsApi } from '@/lib/api/models'
import type { LLMModel } from '@/lib/api/types'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Cpu } from 'lucide-react'

type Props = {
  value?: string | null
  onChange: (modelId: string | undefined) => void
  disabled?: boolean
}

export function SessionModelPicker({ value, onChange, disabled }: Props) {
  const [models, setModels] = useState<LLMModel[]>([])

  useEffect(() => {
    modelsApi.list().then((d) => setModels(d.models)).catch(() => {})
  }, [])

  const defaultModel = models.find((m) => m.is_default)

  return (
    <div className="flex items-center gap-2 text-sm">
      <Cpu className="size-4 text-muted-foreground shrink-0" />
      <Select
        value={value || 'default'}
        onValueChange={(v) => onChange(v === 'default' ? undefined : v)}
        disabled={disabled}
      >
        <SelectTrigger className="h-8 w-[180px]">
          <SelectValue placeholder="默认模型" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="default">
            默认{defaultModel ? ` (${defaultModel.display_name})` : ''}
          </SelectItem>
          {models.map((m) => (
            <SelectItem key={m.id} value={m.id}>
              {m.display_name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}
