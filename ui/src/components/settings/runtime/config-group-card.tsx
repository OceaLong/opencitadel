"use client";

import type { ReactNode } from "react";

type ConfigGroupCardProps = {
  title: string;
  description?: string;
  children: ReactNode;
};

export function ConfigGroupCard({ title, description, children }: ConfigGroupCardProps) {
  return (
    <div className="border-border/70 mb-4 space-y-3 rounded-xl border p-3">
      <div className="space-y-1">
        <p className="text-sm font-medium">{title}</p>
        {description ? <p className="text-muted-foreground text-xs">{description}</p> : null}
      </div>
      {children}
    </div>
  );
}
