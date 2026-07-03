import type { ReactNode } from "react";

type PageHeaderProps = {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
};

export function PageHeader({ title, description, actions }: PageHeaderProps) {
  return (
    <div className="border-border/70 flex items-start justify-between gap-4 border-b px-6 py-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
        {description ? (
          <p className="text-muted-foreground text-sm">{description}</p>
        ) : null}
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </div>
  );
}
