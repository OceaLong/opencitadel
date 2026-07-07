import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

type ScrollablePageContentProps = {
  children: ReactNode;
  className?: string;
};

export function ScrollablePageContent({ children, className }: ScrollablePageContentProps) {
  return (
    <div className="h-full overflow-y-auto">
      <div className={cn("mx-auto flex max-w-4xl flex-col gap-6 p-4 sm:p-6", className)}>{children}</div>
    </div>
  );
}
