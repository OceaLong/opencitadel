'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { Brain, Cpu, Sparkles, ArrowLeft } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'

const NAV = [
  { href: '/settings/models', label: '模型管理', icon: Cpu },
  { href: '/settings/skills', label: 'Skill 模板', icon: Sparkles },
  { href: '/settings/memory', label: '长期记忆', icon: Brain },
]

export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()

  return (
    <div className="min-h-full flex flex-col">
      <header className="flex items-center gap-4 px-6 py-4 border-b">
        <Button variant="ghost" size="sm" asChild>
          <Link href="/">
            <ArrowLeft className="size-4 mr-1" />
            返回
          </Link>
        </Button>
        <h1 className="text-lg font-semibold">设置中心</h1>
      </header>
      <div className="flex flex-1">
        <nav className="w-52 border-r p-4 space-y-1 shrink-0">
          {NAV.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              className={cn(
                'flex items-center gap-2 px-3 py-2 rounded-md text-sm transition-colors',
                pathname === href
                  ? 'bg-primary text-primary-foreground'
                  : 'hover:bg-muted text-muted-foreground hover:text-foreground'
              )}
            >
              <Icon className="size-4" />
              {label}
            </Link>
          ))}
        </nav>
        <main className="flex-1 p-6 overflow-auto">{children}</main>
      </div>
    </div>
  )
}
