import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

import type { Locale } from "@/i18n/routing";
import { translate } from "@/i18n/translate";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const WEEKDAY_KEYS = [
  "common.dates.weekdaySun",
  "common.dates.weekdayMon",
  "common.dates.weekdayTue",
  "common.dates.weekdayWed",
  "common.dates.weekdayThu",
  "common.dates.weekdayFri",
  "common.dates.weekdaySat",
] as const;

/**
 * 将日期字符串格式化为相对日期标签
 */
export function formatRelativeDate(
  dateStr: string | null | undefined,
  locale: Locale,
): string {
  if (!dateStr) return translate("common.dates.today", undefined, locale);
  const date = new Date(dateStr);
  if (isNaN(date.getTime())) return translate("common.dates.today", undefined, locale);
  const now = new Date();

  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const target = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const diffDays = Math.floor((today.getTime() - target.getTime()) / (1000 * 60 * 60 * 24));

  if (diffDays === 0) return translate("common.dates.today", undefined, locale);
  if (diffDays === 1) return translate("common.dates.yesterday", undefined, locale);
  if (diffDays < 7) return translate(WEEKDAY_KEYS[date.getDay()], undefined, locale);

  return date.toLocaleDateString(locale === "zh" ? "zh-CN" : "en-US", {
    month: "numeric",
    day: "numeric",
  });
}

/**
 * 格式化文件大小
 * @param bytes 文件大小（字节）
 * @returns 格式化后的文件大小字符串，如 "2.52 MB"
 */
export function formatFileSize(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`;
}
