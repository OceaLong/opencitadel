"use client";

import Link from "next/link";
import { ArrowUpRight } from "lucide-react";

import { Button } from "@/components/ui/button";

type SettingsPageLinkProps = {
  href: string;
  title: string;
  description: string;
};

export function SettingsPageLink({ href, title, description }: SettingsPageLinkProps) {
  return (
    <div className="border-border/60 bg-card flex flex-col gap-3 rounded-xl border p-4 shadow-[var(--shadow-card)]">
      <div>
        <h3 className="text-foreground text-base font-semibold">{title}</h3>
        <p className="text-muted-foreground mt-1 text-sm">{description}</p>
      </div>
      <Button variant="outline" size="sm" className="w-fit" asChild>
        <Link href={href}>
          打开完整设置页
          <ArrowUpRight className="size-4" />
        </Link>
      </Button>
    </div>
  );
}
