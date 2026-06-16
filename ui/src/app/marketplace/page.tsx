"use client";

import { MarketplaceShell } from "@/components/marketplace/marketplace-shell";

export default function MarketplacePage() {
  return (
    <div className="from-background via-background to-muted/30 flex h-full flex-col bg-gradient-to-br">
      <div className="flex-1 overflow-hidden p-3 sm:p-6">
        <div className="mx-auto h-full w-full max-w-7xl">
          <MarketplaceShell />
        </div>
      </div>
    </div>
  );
}
