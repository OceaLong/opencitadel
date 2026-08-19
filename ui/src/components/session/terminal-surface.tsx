import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

type TerminalSurfaceProps = {
  title?: ReactNode;
  children: ReactNode;
  className?: string;
};

/** 终端外观容器：两主题下恒为深底（--terminal 在 .dark 另有更深覆盖值）。 */
export function TerminalSurface({ title, children, className }: TerminalSurfaceProps) {
  return (
    <div
      className={cn(
        "bg-terminal text-terminal-foreground border-terminal-foreground/15 flex min-h-0 flex-col overflow-hidden rounded-lg border",
        className,
      )}
    >
      {title ? (
        <div className="border-terminal-foreground/15 bg-terminal-muted text-terminal-foreground/60 flex-shrink-0 truncate border-b px-4 py-1.5 text-center text-xs">
          {title}
        </div>
      ) : null}
      {children}
    </div>
  );
}
