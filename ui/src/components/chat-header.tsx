'use client'

import Link from 'next/link'
import {SidebarTrigger, useSidebar} from '@/components/ui/sidebar'
import {ManusSettings} from '@/components/manus-settings'
import {Button} from '@/components/ui/button'
import {Settings2} from 'lucide-react'

export function ChatHeader() {
  const {open, isMobile} = useSidebar()

  return (
    <header className="flex justify-between items-center w-full py-2 px-4 z-50">
      {/* 左侧操作&logo */}
      <div className="flex items-center gap-2">
        {/* 面板操作按钮: 关闭面板&移动端下会显示 */}
        {(!open || isMobile) && <SidebarTrigger className="cursor-pointer"/>}
        {/* Logo占位符 */}
        <Link href="/" className="block bg-white w-[80px] h-9 rounded-md"/>
      </div>
      <div className="flex items-center gap-1">
        <Button variant="ghost" size="sm" asChild>
          <Link href="/settings/models">
            <Settings2 className="size-4 mr-1" />
            设置中心
          </Link>
        </Button>
        <ManusSettings/>
      </div>
    </header>
  )
}