import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

const widthClasses = {
  content: "max-w-content",
  data: "max-w-data",
  full: "max-w-none",
} as const;

type ScrollablePageContentProps = {
  children: ReactNode;
  className?: string;
  width?: keyof typeof widthClasses;
};

export function ScrollablePageContent({
  children,
  className,
  width = "data",
}: ScrollablePageContentProps) {
  return (
    <div className="h-full overflow-y-auto">
      <div
        className={cn(
          "mx-auto flex w-full flex-col gap-6 p-4 sm:p-6",
          widthClasses[width],
          className,
        )}
      >
        {children}
      </div>
    </div>
  );
}
