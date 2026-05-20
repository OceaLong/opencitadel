'use client'

import { useEffect, useState } from 'react'
import { skillsApi } from '@/lib/api/skills'
import type { Skill, SkillSummary } from '@/lib/api/types'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Sparkles } from 'lucide-react'

type Props = {
  value?: string | null
  onChange: (skillId: string | undefined, skill?: SkillSummary | null) => void
  disabled?: boolean
  onSkillLoaded?: (skill: Skill | null) => void
}

export function SessionSkillPicker({ value, onChange, disabled, onSkillLoaded }: Props) {
  const [skills, setSkills] = useState<Skill[]>([])

  useEffect(() => {
    skillsApi.list(true).then((d) => setSkills(d.skills)).catch(() => {})
  }, [])

  useEffect(() => {
    if (!value) {
      onSkillLoaded?.(null)
      return
    }
    const s = skills.find((sk) => sk.id === value)
    if (s) {
      onSkillLoaded?.(s)
    }
  }, [value, skills, onSkillLoaded])

  return (
    <div className="flex items-center gap-2 text-sm">
      <Sparkles className="size-4 text-muted-foreground shrink-0" />
      <Select
        value={value || 'none'}
        onValueChange={(v) => {
          if (v === 'none') {
            onChange(undefined, null)
            onSkillLoaded?.(null)
          } else {
            const s = skills.find((sk) => sk.id === v)
            onChange(v, s ? { id: s.id, name: s.name, icon: s.icon, examples: s.examples } : null)
            onSkillLoaded?.(s || null)
          }
        }}
        disabled={disabled}
      >
        <SelectTrigger className="h-8 w-[160px]">
          <SelectValue placeholder="不启用 Skill" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="none">不启用 Skill</SelectItem>
          {skills.map((s) => (
            <SelectItem key={s.id} value={s.id}>
              {s.icon} {s.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}
