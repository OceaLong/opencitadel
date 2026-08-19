"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { useTranslations } from "next-intl";
import { Loader2, WifiOff, X } from "lucide-react";

import type { VNCStatus } from "@/components/session/vnc-viewer";
import { Button } from "@/components/ui/button";

import { API_CONFIG } from "@/lib/api/fetch";

const VNCViewer = dynamic(
  () => import("@/components/session/vnc-viewer").then((m) => ({ default: m.VNCViewer })),
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
          <div className="bg-terminal/80 absolute inset-0 z-10 flex flex-col items-center justify-center gap-3">
            <Loader2 className="text-terminal-foreground size-8 animate-spin" />
            <span className="text-terminal-foreground/70 text-sm">{t("connectingSandbox")}</span>
          </div>
        )}

        {hasError && (
          <div className="bg-terminal/80 absolute inset-0 z-10 flex flex-col items-center justify-center">
            <div className="border-terminal-foreground/20 bg-terminal/90 flex flex-col items-center gap-3 rounded-2xl border px-10 py-8">
              <WifiOff className="text-terminal-foreground/70 size-10" />
              <div className="text-terminal-foreground text-base font-medium">{t("cannotConnectSandbox")}</div>
              <p className="text-terminal-foreground/70 max-w-[280px] text-center text-sm leading-relaxed">
                {errorDetail || t("sandboxClosedHint")}
              </p>
              <Button
                variant="secondary"
                onClick={onClose}
                className="border-terminal-foreground/25 bg-terminal-foreground/10 text-terminal-foreground hover:bg-terminal-foreground/20 mt-2 min-h-11 cursor-pointer gap-2 rounded-full border px-6"
              >
                <X size={14} />
                {t("exitRemoteDesktop")}
              </Button>
            </div>
          </div>
        )}
      </div>

      {status === "connected" && (
        <div className="pb-safe absolute bottom-6 left-1/2 z-10 -translate-x-1/2">
          <button
            type="button"
            onClick={onClose}
            className="border-terminal-foreground/10 bg-terminal/60 text-terminal-foreground/90 hover:bg-terminal/80 inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-full border px-5 py-2.5 text-sm shadow-xl backdrop-blur transition-colors"
          >
            <X size={14} />
            {t("exitRemoteDesktop")}
          </button>
        </div>
      )}
    </div>
  );
}
