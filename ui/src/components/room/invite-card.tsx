"use client";

import { useEffect, useState } from "react";
import QRCode from "qrcode";
import { Copy, QrCode, Share2, Volume2, VolumeX } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { isRoomSoundsMuted, setRoomSoundsMuted } from "@/components/room/room-sounds";
import { fadeInUp, motion, reducedVariants } from "@/lib/motion";
import { usePrefersReducedMotion } from "@/lib/motion";
import { cn } from "@/lib/utils";

type InviteCardProps = {
  code: string;
  participantCount: number;
  className?: string;
};

export function InviteCard({ code, participantCount, className }: InviteCardProps) {
  const reduced = usePrefersReducedMotion();
  const [qrDataUrl, setQrDataUrl] = useState("");
  const [muted, setMuted] = useState(isRoomSoundsMuted());

  const shareUrl =
    typeof window !== "undefined" ? `${window.location.origin}/room/${code}` : "";

  useEffect(() => {
    if (!shareUrl) return;
    void QRCode.toDataURL(shareUrl, { width: 160, margin: 1 }).then(setQrDataUrl);
  }, [shareUrl]);

  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(shareUrl);
      toast.success("邀请链接已复制");
    } catch {
      toast.error("复制失败");
    }
  };

  const copyCode = async () => {
    try {
      await navigator.clipboard.writeText(code);
      toast.success("房间码已复制");
    } catch {
      toast.error("复制失败");
    }
  };

  const toggleMute = () => {
    const next = !muted;
    setMuted(next);
    setRoomSoundsMuted(next);
  };

  if (participantCount > 1) {
    return (
      <div className={cn("flex justify-end", className)}>
        <Button variant="ghost" size="sm" onClick={toggleMute}>
          {muted ? <VolumeX className="size-4" /> : <Volume2 className="size-4" />}
        </Button>
      </div>
    );
  }

  return (
    <motion.div
      initial="hidden"
      animate="visible"
      variants={reducedVariants(fadeInUp, reduced)}
      className={className}
    >
      <Card className="border-primary/20 bg-gradient-to-br from-primary/5 to-rose-500/5">
        <CardContent className="space-y-3 pt-5">
          <div className="flex items-start justify-between gap-2">
            <div>
              <p className="text-foreground text-sm font-semibold">邀请好友一起玩</p>
              <p className="text-muted-foreground mt-1 text-xs">
                分享链接或扫码加入 · 房间码 {code}
              </p>
            </div>
            <Button variant="ghost" size="icon" className="shrink-0" onClick={toggleMute}>
              {muted ? <VolumeX className="size-4" /> : <Volume2 className="size-4" />}
            </Button>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            {qrDataUrl ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={qrDataUrl} alt="房间二维码" className="size-28 rounded-lg border bg-white p-1" />
            ) : (
              <div className="bg-muted flex size-28 items-center justify-center rounded-lg">
                <QrCode className="text-muted-foreground size-8" />
              </div>
            )}
            <div className="flex min-w-0 flex-1 flex-col gap-2">
              <Button variant="outline" size="sm" onClick={copyLink}>
                <Share2 className="mr-1 size-3.5" />
                复制邀请链接
              </Button>
              <Button variant="outline" size="sm" onClick={copyCode}>
                <Copy className="mr-1 size-3.5" />
                复制房间码
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </motion.div>
  );
}
