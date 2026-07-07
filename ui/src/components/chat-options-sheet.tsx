"use client";

import { SlidersHorizontal } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";

type ChatOptionsSheetProps = {
  children: ReactNode;
};

export function ChatOptionsSheet({ children }: ChatOptionsSheetProps) {
  const t = useTranslations("chatOptions");
  const [open, setOpen] = useState(false);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-8 shrink-0 rounded-full px-2.5 md:hidden"
          aria-label={t("title")}
        >
          <SlidersHorizontal className="size-4" />
          <span className="sr-only">{t("title")}</span>
        </Button>
      </SheetTrigger>
      <SheetContent side="bottom" className="pb-safe rounded-t-2xl">
        <SheetHeader>
          <SheetTitle>{t("title")}</SheetTitle>
        </SheetHeader>
        <div className="mt-4 flex flex-col gap-3">{children}</div>
      </SheetContent>
    </Sheet>
  );
}
