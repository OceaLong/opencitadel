'use client'

import { Sparkles } from 'lucide-react'
import { cn } from '@/lib/utils'

export type ThinkingToggleProps = {
  enabled: boolean
  onChange: (enabled: boolean) => void
  disabled?: boolean
  className?: string
}

export function ThinkingToggle({
  enabled,
  onChange,
  disabled = false,
  className,
}: ThinkingToggleProps) {
  return (
    <button
      type="button"
      aria-pressed={enabled}
      aria-label={enabled ? '关闭思考模式' : '开启思考模式'}
      disabled={disabled}
      onClick={() => onChange(!enabled)}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full h-8 px-3 text-xs border transition-colors shrink-0',
        'disabled:opacity-50 disabled:cursor-not-allowed',
        enabled
          ? 'bg-blue-50 border-blue-200 text-blue-700 shadow-xs'
          : 'bg-white border-gray-200 text-gray-600 hover:bg-gray-50',
        className,
      )}
    >
      <span className="relative inline-flex items-center justify-center">
        <Sparkles className={cn('size-4', enabled ? 'text-blue-500' : 'text-blue-400')} />
        {enabled && (
          <span className="absolute -bottom-0.5 -right-0.5 flex size-3 items-center justify-center rounded-full bg-blue-500 text-[8px] text-white">
            ✓
          </span>
        )}
      </span>
      <span className="font-medium">思考</span>
    </button>
  )
}
