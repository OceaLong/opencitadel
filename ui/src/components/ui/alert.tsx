import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const alertVariants = cva(
  "relative grid w-full gap-1 rounded-lg border border-l-4 px-4 py-3 text-sm",
  {
    variants: {
      variant: {
        info: "border-info-subtle border-l-accent-info bg-info-subtle text-foreground",
        approval:
          "border-approval-subtle border-l-accent-approval bg-approval-subtle text-foreground",
        destructive:
          "border-destructive/30 border-l-accent-destructive bg-destructive/10 text-foreground",
      },
    },
    defaultVariants: { variant: "info" },
  },
);

function Alert({
  className,
  variant,
  ...props
}: React.ComponentProps<"div"> & VariantProps<typeof alertVariants>) {
  return <div role="alert" className={cn(alertVariants({ variant }), className)} {...props} />;
}

function AlertTitle({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("font-medium tracking-tight", className)} {...props} />;
}

function AlertDescription({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("text-muted-foreground text-sm", className)} {...props} />;
}

export { Alert, AlertDescription, AlertTitle };
