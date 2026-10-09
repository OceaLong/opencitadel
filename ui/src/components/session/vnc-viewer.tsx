"use client";

import { useEffect, useRef } from "react";
import { useTranslations } from "next-intl";
import RFB from "@novnc/novnc/lib/rfb";

export type VNCStatus = "connecting" | "connected" | "disconnected" | "error";

type VNCViewerProps = {
  url: string;
  viewOnly?: boolean;
  onStatusChange?: (status: VNCStatus, detail?: string) => void;
};

export function VNCViewer({ url, viewOnly, onStatusChange }: VNCViewerProps) {
  const t = useTranslations("vnc");
  const displayRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!displayRef.current) return;

    onStatusChange?.("connecting");

    let rfb: RFB | null = null;
    let active = true;
    const report = (status: VNCStatus, detail?: string) => {
      if (active) onStatusChange?.(status, detail);
    };
    try {
      rfb = new RFB(displayRef.current, url, {
        credentials: { password: "", username: "", target: "" },
      });

      rfb.viewOnly = viewOnly || false;
      rfb.scaleViewport = true;
      rfb.background = "#000";

      rfb.addEventListener("connect", () => report("connected"));
      rfb.addEventListener("disconnect", (e: CustomEvent) => {
        if (e.detail?.clean) {
          report("disconnected", t("connectionDisconnected"));
        } else {
          report("error", t("sandboxDisconnectedAbnormal"));
        }
      });
      rfb.addEventListener("securityfailure", () => {
        report("error", t("authFailed"));
      });
    } catch {
      report("error", t("connectionFailedNotStarted"));
    }

    return () => {
      active = false;
      try {
        rfb?.disconnect();
      } catch {
        /* noop */
      }
    };
  }, [url, viewOnly, onStatusChange, t]);

  return <div ref={displayRef} style={{ width: "100%", height: "100%", background: "#000" }} />;
}
