"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { Loader2, WifiOff, X } from "lucide-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import type { VNCStatus } from "@/components/vnc-viewer";

import { API_CONFIG } from "@/lib/api/fetch";

const VNCViewer = dynamic(
  () => import("@/components/vnc-viewer").then((m) => ({ default: m.VNCViewer })),
  { ssr: false },
);

export type VNCOverlayProps = {
  sessionId: string;
  onClose: () => void;
};

function buildVNCUrl(sessionId: string): string {
  const apiBase = API_CONFIG.baseURL;

  let host: string;
  let pathname: string;
  let isHttps: boolean;

  try {
    const url = new URL(apiBase);
    host = url.host;
    pathname = url.pathname;
    isHttps = url.protocol === "https:";
  } catch {
    host = window.location.host;
    pathname = apiBase;
    isHttps = window.location.protocol === "https:";
  }

  const protocol = isHttps ? "wss:" : "ws:";
  return `${protocol}//${host}${pathname}/sessions/${sessionId}/vnc`;
}

export function VNCOverlay({ sessionId, onClose }: VNCOverlayProps) {
  const t = useTranslations("vnc");
  const vncUrl = useMemo(() => buildVNCUrl(sessionId), [sessionId]);
  const [status, setStatus] = useState<VNCStatus>("connecting");
  const [errorDetail, setErrorDetail] = useState("");

  const handleStatusChange = useCallback(
    (s: VNCStatus, detail?: string) => {
      setStatus(s);
      if (s === "error" || s === "disconnected") {
        setErrorDetail(detail || t("connectionFailed"));
      }
    },
    [t],
  );

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handleKeyDown);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = "";
    };
  }, [onClose]);

  const hasError = status === "error" || status === "disconnected";

  return (
    <div className="animate-in fade-in fixed inset-0 z-50 flex flex-col bg-black duration-200">
      <div className="relative flex-1">
        <VNCViewer url={vncUrl} viewOnly={false} onStatusChange={handleStatusChange} />

        {status === "connecting" && (
          <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-3 bg-black/80">
            <Loader2 className="size-8 animate-spin text-white" />
            <span className="text-sm text-gray-300">{t("connectingSandbox")}</span>
          </div>
        )}

        {hasError && (
          <div className="absolute inset-0 z-10 flex flex-col items-center justify-center bg-black/80">
            <div className="flex flex-col items-center gap-3 rounded-2xl border border-gray-700 bg-gray-900/90 px-10 py-8">
              <WifiOff className="size-10 text-gray-400" />
              <div className="text-base font-medium text-white">{t("cannotConnectSandbox")}</div>
              <p className="max-w-[280px] text-center text-sm leading-relaxed text-gray-400">
                {errorDetail || t("sandboxClosedHint")}
              </p>
              <Button
                variant="secondary"
                onClick={onClose}
                className="mt-2 cursor-pointer gap-2 rounded-full border border-gray-600 bg-white/10 px-6 text-white hover:bg-white/20"
              >
                <X size={14} />
                {t("exitRemoteDesktop")}
              </Button>
            </div>
          </div>
        )}
      </div>

      {status === "connected" && (
        <div className="absolute bottom-6 left-1/2 z-10 -translate-x-1/2">
          <button
            type="button"
            onClick={onClose}
            className="inline-flex cursor-pointer items-center gap-2 rounded-full border border-white/10 bg-black/60 px-5 py-2 text-sm text-white/90 shadow-xl backdrop-blur transition-colors hover:bg-black/80"
          >
            <X size={14} />
            {t("exitRemoteDesktop")}
          </button>
        </div>
      )}
    </div>
  );
}
