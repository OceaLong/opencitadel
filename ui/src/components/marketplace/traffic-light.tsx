'use client'

import { cn } from '@/lib/utils'

type Light = 'green' | 'yellow' | 'red'

const COLORS: Record<Light, string> = {
  green: 'bg-emerald-500',
  yellow: 'bg-amber-400',
  red: 'bg-red-500',
}

type Props = {
  status: Light
  label: string
  className?: string
}

export function TrafficLight({ status, label, className }: Props) {
  return (
    <div className={cn('flex items-center gap-2', className)}>
      <span className={cn('size-3 rounded-full shrink-0', COLORS[status])} />
      <span className="text-sm text-foreground">{label}</span>
    </div>
  )
}
