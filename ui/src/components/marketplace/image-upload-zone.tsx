"use client";

import { useCallback, useRef, useState } from "react";
import { Camera, ImageIcon, Loader2, Upload } from "lucide-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

import { cn } from "@/lib/utils";

type Props = {
  accept?: string;
  disabled?: boolean;
  loading?: boolean;
  preview?: string | null;
  previewAlt?: string;
  hint?: string;
  onFile: (file: File) => void;
};

export function ImageUploadZone({
  accept = "image/jpeg,image/png",
  disabled = false,
  loading = false,
  preview,
  previewAlt,
  hint,
  onFile,
}: Props) {
  const t = useTranslations("marketplaceApps.imageUpload");
  const fileRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  const handleFile = useCallback(
    (file: File | undefined) => {
      if (file) onFile(file);
    },
    [onFile],
  );

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      if (disabled || loading) return;
      handleFile(e.dataTransfer.files?.[0]);
    },
    [disabled, loading, handleFile],
  );

  return (
    <div className="space-y-3">
      <div
        role="button"
        tabIndex={0}
        onClick={() => !disabled && !loading && fileRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            if (!disabled && !loading) fileRef.current?.click();
          }
        }}
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled && !loading) setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        className={cn(
          "relative cursor-pointer rounded-xl border-2 border-dashed p-6 transition-colors",
          "flex min-h-[140px] flex-col items-center justify-center gap-3 text-center",
          dragOver && "border-primary bg-primary/5",
          !dragOver && "border-muted-foreground/25 hover:border-primary/40 hover:bg-muted/30",
          (disabled || loading) && "pointer-events-none opacity-60",
        )}
      >
        <input
          ref={fileRef}
          type="file"
          accept={accept}
          className="hidden"
          onChange={(e) => handleFile(e.target.files?.[0])}
        />
        {loading ? (
          <Loader2 className="text-muted-foreground size-8 animate-spin" />
        ) : (
          <div className="bg-muted flex size-12 items-center justify-center rounded-full">
            <ImageIcon className="text-muted-foreground size-6" />
          </div>
        )}
        <div className="space-y-1">
          <p className="text-foreground text-sm font-medium">
            {loading ? t("processing") : t("uploadImage")}
          </p>
          <p className="text-muted-foreground max-w-xs text-xs">{hint ?? t("defaultHint")}</p>
        </div>
        {!loading && (
          <div className="flex flex-wrap justify-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={(e) => {
                e.stopPropagation();
                fileRef.current?.click();
              }}
            >
              <Upload className="size-4" />
              {t("selectFile")}
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={(e) => {
                e.stopPropagation();
                fileRef.current?.click();
              }}
            >
              <Camera className="size-4" />
              {t("takePhoto")}
            </Button>
          </div>
        )}
      </div>

      {preview && (
        <div className="bg-muted/20 overflow-hidden rounded-xl border">
          <img src={preview} alt={previewAlt ?? t("previewAlt")} className="max-h-56 w-full object-contain" />
        </div>
      )}
    </div>
  );
}
