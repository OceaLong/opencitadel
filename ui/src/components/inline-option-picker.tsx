'use client'

import { Check, ChevronDown } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Badge } from '@/components/ui/badge'

export type InlineOption = {
  id: string
  title: string
  description?: string
  icon?: React.ReactNode
  badge?: string
  disabled?: boolean
}

type Props = {
  value?: string | null
  options: InlineOption[]
  placeholder: string
  onChange: (id: string | undefined) => void
  disabled?: boolean
  allowClear?: boolean
  clearValue?: string
  className?: string
}

export function InlineOptionPicker({
  value,
  options,
  placeholder,
  onChange,
  disabled,
  allowClear = false,
  clearValue = '__none__',
  className,
}: Props) {
  const selected = options.find((o) => o.id === value)
  const displayLabel = selected?.title ?? placeholder

  const handleSelect = (id: string) => {
    if (allowClear && id === clearValue) {
      onChange(undefined)
      return
    }
    onChange(id)
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={disabled}
          className={cn(
            'h-8 gap-1 px-2 text-xs font-normal text-muted-foreground hover:text-foreground max-w-[160px]',
            className
          )}
        >
          {selected?.icon}
          <span className="truncate">{displayLabel}</span>
          <ChevronDown className="size-3 shrink-0 opacity-60" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-[280px] p-1.5">
        {allowClear && (
          <button
            type="button"
            className={cn(
              'w-full flex items-start gap-3 rounded-lg px-3 py-2.5 text-left transition-colors hover:bg-muted',
              !value && 'bg-muted/60'
            )}
            onClick={() => handleSelect(clearValue)}
          >
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium text-foreground">{placeholder}</div>
            </div>
            {!value && <Check className="size-4 text-primary shrink-0 mt-0.5" />}
          </button>
        )}
        {options.map((option) => {
          const isSelected = value === option.id
          return (
            <button
              key={option.id}
              type="button"
              disabled={option.disabled}
              className={cn(
                'w-full flex items-start gap-3 rounded-lg px-3 py-2.5 text-left transition-colors hover:bg-muted disabled:opacity-50 disabled:pointer-events-none',
                isSelected && 'bg-muted/60'
              )}
              onClick={() => handleSelect(option.id)}
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  {option.icon}
                  <span className="text-sm font-medium text-foreground truncate">
                    {option.title}
                  </span>
                  {option.badge && (
                    <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
                      {option.badge}
                    </Badge>
                  )}
                </div>
                {option.description && (
                  <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">
                    {option.description}
                  </p>
                )}
              </div>
              {isSelected && <Check className="size-4 text-primary shrink-0 mt-0.5" />}
            </button>
          )
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
