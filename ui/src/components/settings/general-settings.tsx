"use client";

import { useTransition } from "react";
import { useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { Languages, SunMoon } from "lucide-react";

import { Item, ItemContent, ItemGroup, ItemMedia, ItemTitle } from "@/components/ui/item";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { authApi } from "@/lib/api/auth";
import { useAuth } from "@/providers/auth-provider";
import { type ThemePreference, useTheme } from "@/providers/theme-provider";

import { setLocale } from "@/i18n/locale";
import { type Locale, locales } from "@/i18n/routing";

import { PasswordForm } from "./password-form";

export function GeneralSettings() {
  const { refresh } = useAuth();
  const tPassword = useTranslations("password");
  const locale = useLocale() as Locale;
  const router = useRouter();
  const tSettings = useTranslations("settings");
  const tLanguage = useTranslations("language");
  const tTheme = useTranslations("theme");
  const { theme, setTheme } = useTheme();
  const [pending, startTransition] = useTransition();

  const handleLocaleChange = (value: string) => {
    const nextLocale = value as Locale;
    if (pending || nextLocale === locale || !locales.includes(nextLocale)) return;
    startTransition(async () => {
      await setLocale(nextLocale);
      router.refresh();
    });
  };

  const handleThemeChange = (value: string) => {
    setTheme(value as ThemePreference);
  };

  return (
    <div className="w-full px-1">
      <ItemGroup className="gap-3">
        <Item variant="outline" size="sm">
          <ItemMedia variant="icon">
            <SunMoon />
          </ItemMedia>
          <ItemContent>
            <ItemTitle>{tSettings("interfaceTheme")}</ItemTitle>
          </ItemContent>
          <Select value={theme} onValueChange={handleThemeChange}>
            <SelectTrigger className="w-[140px] shrink-0">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="system">{tTheme("system")}</SelectItem>
              <SelectItem value="light">{tTheme("light")}</SelectItem>
              <SelectItem value="dark">{tTheme("dark")}</SelectItem>
            </SelectContent>
          </Select>
        </Item>

        <Item variant="outline" size="sm">
          <ItemMedia variant="icon">
            <Languages />
          </ItemMedia>
          <ItemContent>
            <ItemTitle>{tSettings("language")}</ItemTitle>
          </ItemContent>
          <Select value={locale} onValueChange={handleLocaleChange} disabled={pending}>
            <SelectTrigger className="w-[140px] shrink-0">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="zh">{tLanguage("localeZh")}</SelectItem>
              <SelectItem value="en">{tLanguage("localeEn")}</SelectItem>
            </SelectContent>
          </Select>
        </Item>
      </ItemGroup>
      <section className="mt-6 space-y-4 rounded-lg border p-4">
        <h3 className="font-medium">{tPassword("change")}</h3>
        <PasswordForm
          onSubmit={async (newPassword, currentPassword) => {
            await authApi.changePassword(currentPassword, newPassword);
            await refresh();
            // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- Reinitialize the document after changing credentials.
            window.location.href = "/login";
          }}
        />
        <p className="text-muted-foreground text-xs">{tPassword("recovery")}</p>
      </section>
    </div>
  );
}
