'use client'

import Link from 'next/link'
import { ArrowLeft } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { MarketplaceShell } from '@/components/marketplace/marketplace-shell'

export default function MarketplacePage() {
  return (
    <div className="h-full flex flex-col">
      <header className="flex items-center gap-4 px-4 sm:px-6 py-3 border-b bg-background shrink-0">
        <Button variant="ghost" size="sm" asChild>
          <Link href="/">
            <ArrowLeft className="size-4 mr-1" />
            返回
          </Link>
        </Button>
        <span className="text-sm text-muted-foreground">应用市场</span>
      </header>
      <div className="flex-1 overflow-hidden p-4 sm:p-6">
        <div className="w-full h-full max-w-7xl mx-auto">
          <MarketplaceShell />
        </div>
      </div>
    </div>
  )
}
