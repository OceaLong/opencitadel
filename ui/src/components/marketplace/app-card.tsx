'use client'

import { cn } from '@/lib/utils'
import { Card, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import type { MarketplaceApp } from '@/lib/api/types'

type Props = {
  app: MarketplaceApp
  selected?: boolean
  onClick: () => void
}

export function AppCard({ app, selected, onClick }: Props) {
  return (
    <button type="button" onClick={onClick} className="text-left w-full">
      <Card
        className={cn(
          'transition-colors hover:bg-muted/40 cursor-pointer',
          selected && 'ring-2 ring-primary bg-muted/30'
        )}
      >
        <CardHeader className="pb-2">
          <div className="flex items-start justify-between gap-2">
            <CardTitle className="text-base flex items-center gap-2">
              <span>{app.icon}</span>
              {app.name}
            </CardTitle>
            <Badge variant="outline" className="text-[10px] shrink-0">
              {app.category}
            </Badge>
          </div>
          <CardDescription className="line-clamp-2">{app.description}</CardDescription>
        </CardHeader>
      </Card>
    </button>
  )
}
