import { Boxes, Cpu, Plug, Radio, type LucideIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Badge, Button, type BadgeVariant } from "@/components/ui";
import type { ToolOriginKind } from "@/lib/types";
import { cn } from "@/lib/utils";
import { TOOL_ORIGINS } from "./catalog";

const APPEARANCE: Record<ToolOriginKind, { icon: LucideIcon; badge: BadgeVariant }> = {
  core: { icon: Cpu, badge: "neutral" },
  entity: { icon: Boxes, badge: "accent" },
  channel: { icon: Radio, badge: "accent2" },
  external: { icon: Plug, badge: "info" },
};

export function ToolOriginBadge({ kind }: { kind: ToolOriginKind }) {
  const { t } = useTranslation("tools");
  const { icon: Icon, badge } = APPEARANCE[kind];
  return <Badge variant={badge} title={t(`origins.${kind}.hint`)}><Icon size={11} />{t(`origins.${kind}.label`)}</Badge>;
}

/** 来源区块是可组合筛选，数量来自完整目录；重复点击回到全部。 */
export function ToolSources({ value, counts, total, pending, onChange }: {
  value: ToolOriginKind | "all"; counts: Record<ToolOriginKind, number>; total: number; pending: boolean;
  onChange: (kind: ToolOriginKind | "all") => void;
}) {
  const { t } = useTranslation("tools");
  return <section aria-label={t("sourceFilter")} className="space-y-3">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h2 className="text-sm font-semibold text-heading">{t("sourceFilter")}</h2>
      <Button variant="ghost" size="sm" aria-pressed={value === "all"} onClick={() => onChange("all")}>{t("allSources")}<span className="text-muted tabular-nums">{pending ? "—" : total}</span></Button>
    </div>
    <div className="tool-source-grid">
      {TOOL_ORIGINS.map((kind) => {
        const Icon = APPEARANCE[kind].icon;
        return <button key={kind} type="button" aria-pressed={value === kind} aria-label={t(`origins.${kind}.label`)}
          className={cn("tool-source-card", value === kind && "is-active")} onClick={() => onChange(value === kind ? "all" : kind)}>
          <span className="tool-source-icon"><Icon size={18} /></span>
          <span className="min-w-0 flex-1"><span className="block text-sm font-semibold text-heading">{t(`origins.${kind}.label`)}</span>
            <span className="mt-1 block text-xs leading-relaxed text-muted">{t(`origins.${kind}.short`)}</span></span>
          <span className="shrink-0 text-lg font-semibold tabular-nums text-heading">{pending ? "—" : counts[kind]}</span>
        </button>;
      })}
    </div>
  </section>;
}
