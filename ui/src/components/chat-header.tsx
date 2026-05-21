'use client'

import {SidebarTrigger, useSidebar} from '@/components/ui/sidebar'
import {ManusSettings} from '@/components/manus-settings'

export function ChatHeader() {
  const {open, isMobile} = useSidebar()

  return (
    <header className="flex justify-between items-center w-full py-2 px-4 z-50">
      <div className="flex items-center gap-2">
        {(!open || isMobile) && <SidebarTrigger className="cursor-pointer"/>}
        <div className="block bg-white w-[80px] h-9 rounded-md"/>
      </div>
      <div className="flex items-center gap-1">
        <ManusSettings/>
      </div>
    </header>
  )
}
