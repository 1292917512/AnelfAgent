import type { ReactNode } from "react";
import { ChevronRight, Package } from "lucide-react";

export function PluginCard({ title, description, meta, status, onOpen }: {
  title: string; description: string; meta: ReactNode; status?: ReactNode; onOpen: () => void;
}) {
  return <button type="button" onClick={onOpen} aria-label={title}
    className="group flex min-w-0 flex-col gap-3 rounded-xl border border-border bg-card p-4 text-left shadow-sm hover:border-accent/50 hover:bg-hover focus-visible:outline-accent">
    <span className="flex w-full items-center justify-between gap-2">
      <span className="grid size-9 place-items-center rounded-lg bg-accent/10 text-accent"><Package size={19} /></span>
      {status}
    </span>
    <span className="break-words font-semibold text-heading">{title}</span>
    {description && <span className="line-clamp-2 text-xs leading-relaxed text-muted">{description}</span>}
    <span className="mt-auto flex w-full items-center justify-between gap-2 border-t border-border pt-3 text-xs text-muted">
      <span className="min-w-0 truncate">{meta}</span><ChevronRight aria-hidden size={14} />
    </span>
  </button>;
}
