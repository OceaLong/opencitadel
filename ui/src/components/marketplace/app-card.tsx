"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

import type { MarketplaceApp } from "@/lib/api/types";
import { cn } from "@/lib/utils";

type Props = {
  app: MarketplaceApp;
  selected?: boolean;
  compact?: boolean;
  onClick: () => void;
};

export function AppCard({ app, selected, compact, onClick }: Props) {
  return (
    <button type="button" onClick={onClick} className="group w-full text-left">
      <Card
        className={cn(
          "relative cursor-pointer overflow-hidden border transition-all duration-200",
          "hover:border-primary/25 hover:bg-muted/35 hover:shadow-[var(--shadow-card-hover)]",
          selected
            ? "border-primary/45 bg-primary/5 ring-primary/15 shadow-[var(--shadow-card)] ring-1"
            : "border-border/70 bg-card",
          compact && "min-w-[220px] shrink-0",
        )}
      >
        {selected && (
          <span className="bg-primary absolute top-0 bottom-0 left-0 w-1 rounded-l-md" />
        )}
        <CardHeader className={cn("pb-2", compact ? "p-3" : "p-4")}>
          <div className="flex items-start gap-3">
            <span
              className={cn(
                "flex size-10 shrink-0 items-center justify-center rounded-xl text-lg transition-colors",
                selected ? "bg-primary/10" : "bg-muted/70 group-hover:bg-primary/5",
              )}
            >
              {app.icon}
            </span>
            <div className="min-w-0 flex-1 space-y-1.5">
              <div className="flex items-start justify-between gap-2">
                <CardTitle className="text-sm leading-tight font-semibold">{app.name}</CardTitle>
                <Badge variant="secondary" className="shrink-0 text-[10px] font-normal">
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
  );
}
