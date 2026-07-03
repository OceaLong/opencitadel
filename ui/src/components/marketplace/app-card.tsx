"use client";

import { ArrowUpRight, Bot, Eye, Sparkles, Zap } from "lucide-react";
import { useTranslations } from "next-intl";

import { getCategoryLabel } from "@/components/marketplace/app-registry";
import { Badge } from "@/components/ui/badge";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

import type { MarketplaceApp, ModelDependency } from "@/lib/api/types";
import { cn } from "@/lib/utils";

const ACCENTS: Record<string, string> = {
  amber: "from-amber-500/20 via-orange-500/10 to-transparent text-amber-700 dark:text-amber-300",
  blue: "from-blue-500/20 via-cyan-500/10 to-transparent text-blue-700 dark:text-blue-300",
  emerald:
    "from-emerald-500/20 via-teal-500/10 to-transparent text-emerald-700 dark:text-emerald-300",
  indigo:
    "from-indigo-500/20 via-violet-500/10 to-transparent text-indigo-700 dark:text-indigo-300",
  rose: "from-rose-500/20 via-pink-500/10 to-transparent text-rose-700 dark:text-rose-300",
  sky: "from-sky-500/20 via-blue-500/10 to-transparent text-sky-700 dark:text-sky-300",
  violet:
    "from-violet-500/20 via-fuchsia-500/10 to-transparent text-violet-700 dark:text-violet-300",
};

type Props = {
  app: MarketplaceApp;
  selected?: boolean;
  compact?: boolean;
  wide?: boolean;
  modelUnavailable?: boolean;
  onClick: () => void;
};

export function AppCard({ app, selected, compact, wide, modelUnavailable, onClick }: Props) {
  const t = useTranslations("marketplace");
  const tCommon = useTranslations("common");
  const accent = ACCENTS[app.accent] ?? ACCENTS.blue;
  const dependency = app.model_dependency ?? "optional";
  const depBadge = {
    none: { label: t("noModelDirect"), variant: "secondary" as const, icon: Zap },
    optional: { label: t("aiEnhanced"), variant: "outline" as const, icon: Sparkles },
    required: { label: t("modelRequired"), variant: "outline" as const, icon: Bot },
  }[dependency];
  const DepIcon = depBadge.icon;
  const disabled = modelUnavailable && dependency === "required";

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={disabled ? t("modelRequiredUnavailable") : undefined}
      className="group w-full text-left disabled:cursor-not-allowed disabled:opacity-60"
    >
      <Card
        className={cn(
          "relative h-full cursor-pointer overflow-hidden border transition-all duration-300",
          "hover:-translate-y-0.5 hover:border-primary/30 hover:shadow-[var(--shadow-card-hover)]",
          selected
            ? "border-primary/50 bg-primary/5 ring-primary/20 shadow-[var(--shadow-card)] ring-1"
            : "border-border/70 bg-card/90",
          compact && "min-w-[200px] shrink-0",
          wide && "min-h-[140px]",
        )}
      >
        <div className={cn("absolute inset-x-0 top-0 h-16 bg-gradient-to-br", accent)} />
        <CardHeader className={cn("relative", compact ? "p-2.5" : "p-3")}>
          <div className="flex items-start justify-between gap-2">
            <div
              className={cn(
                "bg-background/80 flex size-10 shrink-0 items-center justify-center rounded-xl text-xl shadow-[var(--shadow-card)] ring-1 ring-black/5 backdrop-blur",
                selected && "ring-primary/30",
              )}
            >
              {app.icon}
            </div>
            <ArrowUpRight className="text-muted-foreground size-4 opacity-0 transition-opacity group-hover:opacity-100" />
          </div>

          <div className="mt-2.5 min-w-0 space-y-1.5">
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge variant="secondary" className="text-[10px] font-normal">
                {getCategoryLabel(app.category, t)}
              </Badge>
              {app.featured && (
                <Badge className="bg-primary/10 text-primary hover:bg-primary/10 text-[10px]">
                  <Sparkles className="size-3" />
                  {tCommon("featured")}
                </Badge>
              )}
              {app.needs_vision && (
                <Badge variant="outline" className="text-[10px]">
                  <Eye className="size-3" />
                  {tCommon("vision")}
                </Badge>
              )}
              <Badge variant={depBadge.variant} className="text-[10px]">
                <DepIcon className="size-3" />
                {depBadge.label}
              </Badge>
            </div>
            <CardTitle className="text-sm leading-tight font-semibold">{app.name}</CardTitle>
            <CardDescription className="line-clamp-2 text-xs leading-relaxed">
              {app.description}
            </CardDescription>
            {app.tags.length > 0 && (
              <div className="flex flex-wrap gap-1 pt-1">
                {app.tags.slice(0, wide ? 4 : 3).map((tag) => (
                  <span
                    key={tag}
                    className="text-muted-foreground bg-muted/60 rounded-full px-2 py-0.5 text-[10px]"
                  >
                    {tag}
                  </span>
                ))}
              </div>
            )}
          </div>
        </CardHeader>
      </Card>
    </button>
  );
}
