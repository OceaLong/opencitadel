import en from "../../messages/en.json";
import zh from "../../messages/zh.json";

import type { Locale } from "./routing";

type MessageTree = Record<string, unknown>;

const messagesByLocale: Record<Locale, MessageTree> = {
  en: en as MessageTree,
  zh: zh as MessageTree,
};

function getLocaleFromDocument(): Locale {
  if (typeof document === "undefined") return "en";
  const match = document.cookie
    .split("; ")
    .find((part) => part.startsWith(`${encodeURIComponent("NEXT_LOCALE")}=`));
  const value = match ? decodeURIComponent(match.split("=").slice(1).join("=")) : "";
  return value === "zh" ? "zh" : "en";
}

function resolveKey(tree: MessageTree, key: string): string | undefined {
  const parts = key.split(".");
  let current: unknown = tree;
  for (const part of parts) {
    if (!current || typeof current !== "object" || !(part in (current as MessageTree))) {
      return undefined;
    }
    current = (current as MessageTree)[part];
  }
  return typeof current === "string" ? current : undefined;
}

export function getClientLocale(): Locale {
  return getLocaleFromDocument();
}

export function translate(
  key: string,
  values?: Record<string, string | number | boolean>,
  locale?: Locale,
): string {
  const resolvedLocale = locale ?? getLocaleFromDocument();
  const template = resolveKey(messagesByLocale[resolvedLocale], key) ?? key;
  if (!values) return template;

  return template.replace(/\{(\w+)\}/g, (_, name: string) => {
    const value = values[name];
    return value === undefined ? `{${name}}` : String(value);
  });
}
