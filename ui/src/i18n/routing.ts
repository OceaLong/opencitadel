import { defineRouting } from "next-intl/routing";

export const locales = ["en", "zh"] as const;
export type Locale = (typeof locales)[number];

export const LOCALE_COOKIE_NAME = "NEXT_LOCALE";

export const routing = defineRouting({
  locales,
  defaultLocale: "en",
  localePrefix: "never",
  localeCookie: {
    name: LOCALE_COOKIE_NAME,
  },
});
