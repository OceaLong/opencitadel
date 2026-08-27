import type { Locale } from "@/i18n/routing";
import { translate } from "@/i18n/translate";

export function modelErrorMessage(code: string | null | undefined, locale?: Locale): string | null {
  if (!code) return null;
  if (code === "MODEL_QUOTA_EXCEEDED") {
    return translate("errors.modelQuotaExceeded", undefined, locale);
  }
  if (code === "MODEL_INVALID_REQUEST") {
    return translate("errors.modelInvalidRequest", undefined, locale);
  }
  if (code.startsWith("MODEL_") || code.startsWith("EMBEDDING_")) {
    return translate("errors.modelUnavailable", undefined, locale);
  }
  if (code === "INFRASTRUCTURE_FAILED") {
    return translate("errors.infraFailed", undefined, locale);
  }
  return null;
}
