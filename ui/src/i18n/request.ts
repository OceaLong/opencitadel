import { cookies, headers } from "next/headers";
import { getRequestConfig } from "next-intl/server";

import { detectLocaleFromLanguageTag } from "./detect-locale";
import { LOCALE_COOKIE_NAME, routing, type Locale } from "./routing";

export default getRequestConfig(async () => {
  const store = await cookies();
  const cookieLocale = store.get(LOCALE_COOKIE_NAME)?.value;

  let locale: Locale = routing.defaultLocale;

  if (cookieLocale && routing.locales.includes(cookieLocale as Locale)) {
    locale = cookieLocale as Locale;
  } else {
    const headerStore = await headers();
    locale = detectLocaleFromLanguageTag(headerStore.get("accept-language"));
  }

  return {
    locale,
    messages: (await import(`../../messages/${locale}.json`)).default,
  };
});
