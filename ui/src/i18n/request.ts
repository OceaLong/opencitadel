import { cookies, headers } from "next/headers";
import { getRequestConfig } from "next-intl/server";

import { routing, type Locale } from "./routing";

function detectLocaleFromAcceptLanguage(acceptLanguage: string | null): Locale {
  if (!acceptLanguage) return routing.defaultLocale;

  const preferred = acceptLanguage
    .split(",")
    .map((part) => part.trim().split(";")[0]?.toLowerCase())
    .find(Boolean);

  if (preferred?.startsWith("zh")) {
    return "zh";
  }

  return routing.defaultLocale;
}

export default getRequestConfig(async () => {
  const store = await cookies();
  const cookieLocale = store.get("NEXT_LOCALE")?.value;

  let locale: Locale = routing.defaultLocale;

  if (
    cookieLocale &&
    routing.locales.includes(cookieLocale as Locale)
  ) {
    locale = cookieLocale as Locale;
  } else {
    const headerStore = await headers();
    locale = detectLocaleFromAcceptLanguage(headerStore.get("accept-language"));
  }

  return {
    locale,
    messages: (await import(`../../messages/${locale}.json`)).default,
  };
});
