import { useDialogSession } from "@/hooks/useDialogSession";
import type { TOptions } from "i18next";
import { ArrowRight, ArrowUpRight, Trash2 } from "lucide-react";
import { Link } from "react-router-dom";
import type { UnifiedTag } from "@/lib/types";
import { Badge, Button, Modal } from "@/components/ui";

type TagTranslate = (key: string, options?: TOptions) => string;

function tagDisplayName(tag: UnifiedTag, t: TagTranslate) {
  return tag.message_key ? "[" + tag.name + ":" + t("previewValue") + "]" : tag.name;
}

function TagBadges({ tag, t }: { tag: UnifiedTag; t: TagTranslate }) {
  return <>
    <Badge variant={tag.builtin ? "neutral" : "accent"}>{t(tag.builtin ? "builtin" : "custom")}</Badge>
    {tag.message_key && <Badge variant="accent">{t("sourceMessage")}</Badge>}
    {tag.sources.includes("tool") && <Badge variant="neutral">{t("sourceTool")}</Badge>}
  </>;
}

/** 标签目录仅展示定长摘要，完整信息由二级详情承载。 */
export function TagCard({ tag, onOpen, t }: {
  tag: UnifiedTag; onOpen: () => void; t: TagTranslate;
}) {
  const displayName = tagDisplayName(tag, t);
  const description = tag.description || t("routingHint");
  return <article className="min-w-0">
    <button type="button" onClick={onOpen} aria-label={t("viewDetailsNamed", { name: tag.name })}
      className="group flex h-full w-full flex-col rounded-xl border border-border bg-card p-4 text-left transition-colors hover:border-accent/50 hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
      <code className="line-clamp-2 h-10 w-full break-all text-sm font-semibold leading-5 text-heading" title={displayName}>{displayName}</code>
      <div className="mt-2 flex flex-wrap gap-1.5"><TagBadges tag={tag} t={t} /></div>
      <p className="my-4 line-clamp-2 h-10 break-words text-sm leading-5 text-muted" title={description}>{description}</p>
      <div className="mt-auto flex w-full items-center justify-between gap-2 border-t border-border/60 pt-3">
        <span className="text-xs tabular-nums text-muted">{t("linkedTools", { count: tag.tool_count })}</span>
        <span className="inline-flex shrink-0 items-center gap-1 text-xs text-accent">{t("viewDetails")}<ArrowRight size={14} /></span>
      </div>
    </button>
  </article>;
}

/** 标签详情展示完整描述与关联工具，并承载自定义标签的删除入口。 */
export function TagDetailModal({ tag, onClose, onDelete, t }: {
  tag: UnifiedTag; onClose: () => void; onDelete?: () => void; t: TagTranslate;
}) {
  const description = tag.description || t("routingHint");
  const session = useDialogSession(onClose);
  return <Modal {...session.surfaceProps} onClose={session.close} width="max-w-2xl"
    title={<code className="break-all font-mono text-sm">{tagDisplayName(tag, t)}</code>}
    footer={<div className="flex w-full items-center justify-between gap-2">
      <div>{!tag.builtin && onDelete && <Button size="sm" variant="danger" disabled={tag.tool_count > 0}
        aria-label={t("deleteNamed", { name: tag.name })} title={t(tag.tool_count ? "inUse" : "common:delete")} onClick={onDelete}>
        <Trash2 size={14} />{t("common:delete")}
      </Button>}</div>
      <Button size="sm" variant="secondary" onClick={session.close}>{t("common:close")}</Button>
    </div>}>
    <div className="space-y-5">
      <div className="flex flex-wrap gap-1.5"><TagBadges tag={tag} t={t} /></div>
      <section>
        <h3 className="text-xs font-medium text-muted">{t("tagDescription")}</h3>
        <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-relaxed text-foreground">{description}</p>
      </section>
      <section className="border-t border-border/60 pt-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-medium text-heading">{t("linkedTools", { count: tag.tool_count })}</h3>
          {tag.tool_count > 0 && <Link className="inline-flex items-center gap-1 text-xs font-medium text-accent hover:underline"
            to={"/tools?tag=" + encodeURIComponent(tag.name)}>{t("viewInTools")}<ArrowUpRight size={13} /></Link>}
        </div>
        {tag.tools.length > 0 ? <ul className="mt-3 grid gap-2 sm:grid-cols-2">
          {tag.tools.map((tool) => <li key={tool} className="min-w-0 rounded-lg border border-border/60 bg-secondary px-3 py-2">
            <code className="block break-all text-xs leading-5 text-foreground">{tool}</code>
          </li>)}
        </ul> : <p className="mt-3 text-sm text-muted">{t("noLinkedTools")}</p>}
      </section>
    </div>
  </Modal>;
}
