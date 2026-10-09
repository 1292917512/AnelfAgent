import type { ReactNode } from "react";
import { Search, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Input, Button } from "@/components/ui";

export function ListToolbar({ search, onSearch, count, children }: {
  search: string; onSearch: (value: string) => void; count: number; children?: ReactNode;
}) {
  const { t } = useTranslation("common");
  return (
    <div className="flex flex-wrap items-center gap-3">
      <div className="relative min-w-40 flex-1 sm:max-w-sm">
        <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
        <Input type="search" value={search} onChange={(event) => onSearch(event.target.value)}
          aria-label={t("search")} placeholder={t("search")} className="w-full pl-9 pr-9" />
        {search && <Button variant="ghost" size="icon" className="absolute right-1 top-1/2 -translate-y-1/2"
          title={t("clearFilters")} onClick={() => onSearch("")}><X size={14} /></Button>}
      </div>
      <span className="text-xs text-muted tabular-nums" role="status">{t("resultsCount", { count })}</span>
      <div className="ml-auto flex flex-wrap items-center gap-2">{children}</div>
    </div>
  );
}
