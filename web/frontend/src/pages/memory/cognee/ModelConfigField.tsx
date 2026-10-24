import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import * as Popover from "@radix-ui/react-popover";
import { Info } from "lucide-react";
import { PopoverContent } from "@/components/ui/PopoverContent";

/** Compact model field with help available by hover, keyboard or touch. */
export function ModelConfigField({ label, desc, children }: {
  label: string; desc?: string; children: ReactNode;
}) {
  const { t } = useTranslation("memory");
  return <div className="relative min-w-0">
    <label className="block space-y-1">
      <span className="block min-h-8 pr-6 text-xs font-medium text-muted">{label}</span>
      {children}
    </label>
    {desc && <Popover.Root>
      <Popover.Trigger asChild>
        <button type="button" title={desc} aria-label={t("cognee.fieldHelp", { label })} className="absolute -right-1 -top-1 rounded p-1 text-muted hover:text-accent focus-visible:outline-accent">
          <Info size={14} aria-hidden />
        </button>
      </Popover.Trigger>
      <PopoverContent className="w-72 p-3 text-xs leading-relaxed" aria-label={label}>{desc}</PopoverContent>
    </Popover.Root>}
  </div>;
}
