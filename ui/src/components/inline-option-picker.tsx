"use client";

import { Check, ChevronDown } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

import { cn } from "@/lib/utils";

export type InlineOption = {
  id: string;
  title: string;
  description?: string;
  icon?: React.ReactNode;
  badge?: string;
  disabled?: boolean;
};

type Props = {
  value?: string | null;
  options: InlineOption[];
  placeholder: string;
  onChange: (id: string | undefined) => void;
  disabled?: boolean;
  allowClear?: boolean;
  clearValue?: string;
  className?: string;
};

export function InlineOptionPicker({
  value,
  options,
  placeholder,
  onChange,
  disabled,
  allowClear = false,
  clearValue = "__none__",
  className,
}: Props) {
  const selected = options.find((o) => o.id === value);
  const displayLabel = selected?.title ?? placeholder;

  const handleSelect = (id: string) => {
    if (allowClear && id === clearValue) {
      onChange(undefined);
      return;
    }
    onChange(id);
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={disabled}
          className={cn(
            "text-muted-foreground hover:text-foreground h-8 max-w-[160px] gap-1 px-2 text-xs font-normal",
            className,
          )}
        >
          {selected?.icon}
          <span className="truncate">{displayLabel}</span>
          <ChevronDown className="size-3 shrink-0 opacity-60" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-[280px] p-1.5">
        {allowClear && (
          <button
            type="button"
            className={cn(
              "hover:bg-muted flex w-full items-start gap-3 rounded-lg px-3 py-2.5 text-left transition-colors",
              !value && "bg-muted/60",
            )}
            onClick={() => handleSelect(clearValue)}
          >
            <div className="min-w-0 flex-1">
              <div className="text-foreground text-sm font-medium">{placeholder}</div>
            </div>
            {!value && <Check className="text-primary mt-0.5 size-4 shrink-0" />}
          </button>
        )}
        {options.map((option) => {
          const isSelected = value === option.id;
          return (
            <button
              key={option.id}
              type="button"
              disabled={option.disabled}
              className={cn(
                "hover:bg-muted flex w-full items-start gap-3 rounded-lg px-3 py-2.5 text-left transition-colors disabled:pointer-events-none disabled:opacity-50",
                isSelected && "bg-muted/60",
              )}
              onClick={() => handleSelect(option.id)}
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  {option.icon}
                  <span className="text-foreground truncate text-sm font-medium">
                    {option.title}
                  </span>
                  {option.badge && (
                    <Badge variant="secondary" className="px-1.5 py-0 text-2xs">
                      {option.badge}
                    </Badge>
                  )}
                </div>
                {option.description && (
                  <p className="text-muted-foreground mt-0.5 line-clamp-2 text-xs">
                    {option.description}
                  </p>
                )}
              </div>
              {isSelected && <Check className="text-primary mt-0.5 size-4 shrink-0" />}
            </button>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
