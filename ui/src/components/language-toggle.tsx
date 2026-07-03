"use client";

import { useTransition } from "react";
import { useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { setLocale } from "@/i18n/locale";
import { type Locale, locales } from "@/i18n/routing";

export function LanguageToggle() {
  const locale = useLocale() as Locale;
  const router = useRouter();
  const t = useTranslations("language");
  const [pending, startTransition] = useTransition();

  const nextLocale: Locale = locale === "zh" ? "en" : "zh";
  const shortLabel = nextLocale === "zh" ? t("localeShortZh") : t("localeShortEn");
  const switchLabel = nextLocale === "zh" ? t("localeZh") : t("localeEn");

  const handleToggle = () => {
    if (pending || !locales.includes(nextLocale)) return;
    startTransition(async () => {
      await setLocale(nextLocale);
      router.refresh();
    });
  };

  return (
    <Button
      type="button"
      variant="outline"
      size="icon-sm"
      onClick={handleToggle}
      disabled={pending}
      aria-label={switchLabel}
      title={switchLabel}
      className="text-2xs min-w-8 font-semibold"
    >
      {shortLabel}
    </Button>
  );
}
