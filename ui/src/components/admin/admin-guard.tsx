"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";

import { useAuth } from "@/providers/auth-provider";

export function AdminGuard({ children }: { children: ReactNode }) {
  const t = useTranslations("adminNav");
  const tCommon = useTranslations("common");
  const { user, loading } = useAuth();

  const canAccess = user?.global_role === "admin" || user?.global_role === "auditor";

  if (loading) {
    return (
      <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
        {tCommon("loading")}
      </div>
    );
  }

  if (!canAccess) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 p-6">
        <p className="text-muted-foreground text-sm">{t("forbidden")}</p>
        <Button variant="outline" asChild>
          <Link href="/">{tCommon("backHome")}</Link>
        </Button>
      </div>
    );
  }

  return <>{children}</>;
}
