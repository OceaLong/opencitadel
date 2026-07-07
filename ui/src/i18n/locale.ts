"use server";

import { cookies } from "next/headers";

import { LOCALE_COOKIE_NAME, type Locale, locales } from "./routing";

export async function setLocale(locale: Locale) {
  if (!locales.includes(locale)) {
    throw new Error(`Invalid locale: ${locale}`);
  }

  const cookieStore = await cookies();
  cookieStore.set(LOCALE_COOKIE_NAME, locale, {
    path: "/",
    sameSite: "lax",
  });
}
