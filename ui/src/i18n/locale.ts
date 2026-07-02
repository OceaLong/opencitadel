"use server";

import { cookies } from "next/headers";

import { type Locale, locales, routing } from "./routing";

export async function setLocale(locale: Locale) {
  if (!locales.includes(locale)) {
    throw new Error(`Invalid locale: ${locale}`);
  }

  const cookieStore = await cookies();
  cookieStore.set("NEXT_LOCALE", locale, {
    path: "/",
    sameSite: "lax",
  });
}
