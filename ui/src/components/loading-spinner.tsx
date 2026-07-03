import { cn } from "@/lib/utils";
import { IconLoading } from "@/lib/icons";

type LoadingSpinnerProps = {
  className?: string;
  label?: string;
};

export function LoadingSpinner({ className, label }: LoadingSpinnerProps) {
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <IconLoading className="size-4 animate-spin" aria-hidden />
      {label ? <span className="text-muted-foreground text-sm">{label}</span> : null}
    </span>
  );
}
