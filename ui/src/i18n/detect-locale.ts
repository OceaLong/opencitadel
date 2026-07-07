import { LOCALE_COOKIE_NAME, routing, type Locale } from "./routing";

/** Map a BCP 47 language tag (or Accept-Language value) to a supported locale. */
export function detectLocaleFromLanguageTag(tag: string | null | undefined): Locale {
  if (!tag) return routing.defaultLocale;

  const preferred = tag
    .split(",")
    .map((part) => part.trim().split(";")[0]?.toLowerCase())
    .find(Boolean);

  if (preferred?.startsWith("zh")) {
    return "zh";
  }

  return routing.defaultLocale;
}

export function readLocaleCookie(): Locale | null {
  if (typeof document === "undefined") return null;

  const match = document.cookie
    .split("; ")
    .find((part) => part.startsWith(`${encodeURIComponent(LOCALE_COOKIE_NAME)}=`));
  const value = match ? decodeURIComponent(match.split("=").slice(1).join("=")) : "";
  if (value && routing.locales.includes(value as Locale)) {
    return value as Locale;
  }
  return null;
}

/** Client-side locale resolution: cookie, then navigator.language, then default. */
export function getClientLocale(): Locale {
  const cookieLocale = readLocaleCookie();
  if (cookieLocale) return cookieLocale;

  if (typeof navigator !== "undefined" && navigator.language) {
    return detectLocaleFromLanguageTag(navigator.language);
  }

  return routing.defaultLocale;
}
