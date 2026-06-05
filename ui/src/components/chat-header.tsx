'use client'

import Link from 'next/link'
import {LayoutGrid} from 'lucide-react'
import {SidebarTrigger, useSidebar} from '@/components/ui/sidebar'
import {ManusSettings} from '@/components/manus-settings'
import {Button} from '@/components/ui/button'

export function ChatHeader() {
  const {open, isMobile} = useSidebar()

  return (
    <header className="flex justify-between items-center w-full py-2 px-4 z-50">
      <div className="flex items-center gap-2">
        {(!open || isMobile) && <SidebarTrigger className="cursor-pointer"/>}
        <div className="block bg-white w-[80px] h-9 rounded-md"/>
      </div>
      <div className="flex items-center gap-1">
        <Button variant="outline" size="icon-sm" asChild title="应用市场">
          <Link href="/marketplace">
            <LayoutGrid className="size-4" />
          </Link>
        </Button>
        <ManusSettings/>
      </div>
    </header>
  )
}
