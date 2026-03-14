import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { statusApi } from "@/lib/api";
import { Card } from "@/components/common/Card";

export function EventsPanel() {
  const { t } = useTranslation("status");
  const { data: events } = useQuery({ queryKey: ["events"], queryFn: () => statusApi.events().then((r) => r.data), refetchInterval: 10000 });

  return (
    <Card title={t("eventStats")}>
      {events?.stats && Object.keys(events.stats).length > 0 ? (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
          {Object.entries(events.stats as Record<string, number>).map(([name, count]) => (
            <div key={name} className="flex items-center justify-between py-2 px-3 rounded-[var(--radius-md)] bg-[var(--bg-elevated)] border border-[var(--border)]">
              <span className="text-xs text-[var(--muted)] truncate" title={name}>{t(`eventNames.${name}`, { defaultValue: name })}</span>
              <span className="text-sm font-semibold text-[var(--text-strong)] ml-2">{count}</span>
            </div>
          ))}
        </div>
      ) : <p className="text-[var(--muted)] text-sm">{t("noEventData")}</p>}
    </Card>
  );
}
