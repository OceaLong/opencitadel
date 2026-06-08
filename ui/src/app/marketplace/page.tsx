"use client";

import Link from "next/link";
import { ArrowLeft } from "lucide-react";

import { MarketplaceShell } from "@/components/marketplace/marketplace-shell";
import { Button } from "@/components/ui/button";

export default function MarketplacePage() {
  return (
    <div className="bg-background flex h-full flex-col">
      <header className="border-border/70 bg-card/80 flex shrink-0 items-center gap-4 border-b px-4 py-3 sm:px-6">
        <Button variant="ghost" size="sm" asChild>
          <Link href="/">
            <ArrowLeft className="mr-1 size-4" />
            返回
          </Link>
        </Button>
        <span className="text-muted-foreground text-sm">应用市场</span>
      </header>
      <div className="flex-1 overflow-hidden p-4 sm:p-6">
        <div className="mx-auto h-full w-full max-w-7xl">
          <MarketplaceShell />
        </div>
      </div>
    </div>
  );
}
