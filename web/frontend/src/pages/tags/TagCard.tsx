import type { TOptions } from "i18next";
import { ArrowUpRight, Trash2 } from "lucide-react";
import { Link } from "react-router-dom";
import type { UnifiedTag } from "@/lib/types";
import { Badge, Button } from "@/components/ui";

export function TagCard({ tag, onDelete, t }: {
  tag: UnifiedTag; onDelete?: () => void; t: (key: string, options?: TOptions) => string;
}) {
  return <article className="rounded-xl border border-border bg-card p-4">
    <div className="flex items-start gap-3">
      <div className="min-w-0 flex-1">
        <code className="break-all text-sm font-semibold text-heading">{tag.message_key ? "[" + tag.name + ":" + t("previewValue") + "]" : tag.name}</code>
        <div className="mt-2 flex flex-wrap gap-1.5">
          <Badge variant={tag.builtin ? "neutral" : "accent"}>{t(tag.builtin ? "builtin" : "custom")}</Badge>
          {tag.message_key && <Badge variant="accent">{t("sourceMessage")}</Badge>}
          {tag.sources.includes("tool") && <Badge variant="neutral">{t("sourceTool")}</Badge>}
        </div>
      </div>
      {!tag.builtin && onDelete && <Button size="icon" variant="ghost" disabled={tag.tool_count > 0}
        aria-label={t("deleteNamed", { name: tag.name })} title={t(tag.tool_count ? "inUse" : "common:delete")} onClick={onDelete}><Trash2 size={14} /></Button>}
    </div>
    <p className="mt-3 break-words text-sm leading-relaxed text-muted">{tag.description || t("routingHint")}</p>
    {tag.tool_count > 0 && <div className="mt-4 border-t border-border pt-3">
      <Link className="inline-flex items-center gap-1 text-xs font-medium text-accent hover:underline" to={"/tools?tag=" + encodeURIComponent(tag.name)}>
        {t("linkedTools", { count: tag.tool_count })}<ArrowUpRight size={13} />
      </Link>
      <p className="mt-2 break-words font-mono text-xs leading-relaxed text-muted">{tag.tools.slice(0, 5).join(" · ")}{tag.tools.length > 5 ? " …" : ""}</p>
    </div>}
  </article>;
}
