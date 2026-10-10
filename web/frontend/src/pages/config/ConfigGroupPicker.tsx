import { useState } from "react";
import { Check, ChevronDown, Search } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Modal } from "@/components/ui/Modal";
import type { ConfigModuleNode } from "./configTree";

export function ConfigGroupPicker({ tree, active, onSelect }: {
  tree: ConfigModuleNode[]; active: string | null; onSelect: (group: string) => void;
}) {
  const { t } = useTranslation("config");
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const label = (group: string) => t(`sections.${group}`, { defaultValue: group.split("/").pop() ?? group });
  const modules = tree.map((module) => ({ ...module, sections: module.sections.filter((section) =>
    `${t(`modules.${module.module}`, { defaultValue: module.module })} ${label(section.group)} ${section.group}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()),
  ) })).filter((module) => module.sections.length);
  return <div className="md:hidden">
    <button type="button" onClick={() => setOpen(true)} className="flex min-h-12 w-full items-center gap-3 rounded-xl border border-border bg-card px-4 py-3 text-left" aria-label={t("selectGroup")}>
      <span className="text-xs text-muted">{t("groupLabel")}</span><span className="min-w-0 flex-1 truncate text-sm font-medium text-heading">{active ? label(active) : t("selectGroup")}</span><ChevronDown size={16} className="text-muted" />
    </button>
    <Modal open={open} onClose={() => setOpen(false)} title={t("selectGroup")}>
      <div className="relative mb-4"><Search size={16} className="absolute left-3 top-3.5 text-muted" /><input aria-label={t("filterGroups")} placeholder={t("filterGroups")} value={query} onChange={(event) => setQuery(event.target.value)} className="h-12 w-full rounded-xl border border-input bg-elevated pl-10 pr-3 outline-none focus:border-accent" /></div>
      <div className="space-y-5">
        {modules.map((module) => <section key={module.module}>
          <h3 className="mb-2 text-xs font-semibold text-muted">{t(`modules.${module.module}`, { defaultValue: module.module })}</h3>
          <div className="grid grid-cols-2 gap-2">{module.sections.map((section) => <button type="button" key={section.group} aria-pressed={active === section.group}
            onClick={() => { onSelect(section.group); setOpen(false); setQuery(""); }}
            className="flex min-h-14 items-center gap-2 rounded-xl border border-border px-3 py-2 text-left text-sm aria-pressed:border-accent aria-pressed:bg-accent-subtle aria-pressed:text-accent">
            <span className="min-w-0 flex-1 break-words">{label(section.group)}</span>{active === section.group ? <Check size={15} /> : <span className="text-xs text-muted">{section.items.length}</span>}
          </button>)}</div>
        </section>)}
        {!modules.length && <p role="status" className="py-6 text-center text-sm text-muted">{t("noResult")}</p>}
      </div>
    </Modal>
  </div>;
}
