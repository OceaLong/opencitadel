import Link from "next/link";
import { getTranslations } from "next-intl/server";

import { Button } from "@/components/ui/button";

export default async function NotFound() {
  const t = await getTranslations("errors");
  const tCommon = await getTranslations("common");

  return (
    <div className="bg-background flex h-full min-h-[60vh] flex-col items-center justify-center gap-4 px-4 text-center">
      <p className="text-muted-foreground font-mono text-5xl font-semibold" translate="no">
        404
      </p>
      <h1 className="text-lg font-semibold">{t("pageNotFound")}</h1>
      <p className="text-muted-foreground max-w-md text-sm">{t("pageNotFoundDescription")}</p>
      <Button asChild variant="outline">
        <Link href="/">{tCommon("backHome")}</Link>
      </Button>
    </div>
  );
}
