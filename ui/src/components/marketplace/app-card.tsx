'use client'

import { cn } from '@/lib/utils'
import { Card, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import type { MarketplaceApp } from '@/lib/api/types'

type Props = {
  app: MarketplaceApp
  selected?: boolean
  compact?: boolean
  onClick: () => void
}

export function AppCard({ app, selected, compact, onClick }: Props) {
  return (
    <button type="button" onClick={onClick} className="text-left w-full group">
      <Card
        className={cn(
          'relative overflow-hidden transition-all duration-200 cursor-pointer border',
          'hover:shadow-sm hover:border-primary/30 hover:bg-muted/20',
          selected
            ? 'border-primary/50 bg-primary/5 shadow-sm ring-1 ring-primary/20'
            : 'border-border/60 bg-white',
          compact && 'min-w-[220px] shrink-0'
        )}
      >
        {selected && (
          <span className="absolute left-0 top-0 bottom-0 w-1 bg-primary rounded-l-md" />
        )}
        <CardHeader className={cn('pb-2', compact ? 'p-3' : 'p-4')}>
          <div className="flex items-start gap-3">
            <span
              className={cn(
                'flex size-10 shrink-0 items-center justify-center rounded-lg text-lg transition-colors',
                selected ? 'bg-primary/10' : 'bg-muted group-hover:bg-primary/5'
              )}
            >
              {app.icon}
            </span>
            <div className="min-w-0 flex-1 space-y-1.5">
              <div className="flex items-start justify-between gap-2">
                <CardTitle className="text-sm font-semibold leading-tight">
                  {app.name}
                </CardTitle>
                <Badge variant="secondary" className="text-[10px] shrink-0 font-normal">
                  {app.category}
                </Badge>
              </div>
              <CardDescription className="line-clamp-2 text-xs leading-relaxed">
                {app.description}
              </CardDescription>
            </div>
          </div>
        </CardHeader>
      </Card>
    </button>
  )
}
