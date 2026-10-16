import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { RecallTestItem } from "@/lib/types";

export function RecallAttribution({ item }: { item: RecallTestItem }) {
  const { t } = useTranslation("memory");
  const [open, setOpen] = useState(false);
  const origins = item.provenance.origins ?? [];
  const date = (value: number) => new Date(value * 1000).toLocaleString();
  return (
    <details className="mt-2 text-xs text-muted" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary className="cursor-pointer select-none">
        {t("recall.attribution")} · {item.id}
        {item.provenance.activity_date && ` · ${item.provenance.activity_date}`}
        {item.sensitivity && item.sensitivity !== "normal" && ` · ${t("recall.private")}`}
      </summary>
      {open && <div className="mt-2 space-y-2 rounded border border-border p-2 break-words">
        <p>{t("recall.attributionHint")}</p>
        {item.timestamp ? <p>{t("recall.recordedAt", { time: date(item.timestamp) })}</p> : null}
        {item.source === "cognee_graph" && <p>{t("recall.graphInference")}</p>}
        {item.path ? <p className="font-mono">{item.path}:{item.start_line ?? 0}-{item.end_line ?? 0}</p>
          : origins.length === 0 && <p>{t("recall.unknownOrigin")}</p>}
        {origins.map((origin, index) => (
          <div key={index} className="space-y-1 border-l border-border pl-2">
            <p>{t(origin.kind === "conversation_summary" ? "recall.conversationSummary" : "recall.agentNote")}</p>
            <p className="font-mono">{origin.scope}</p>
            {origin.started_at ? <p>{date(origin.started_at)}
              {origin.ended_at && origin.ended_at !== origin.started_at ? ` — ${date(origin.ended_at)}` : ""}</p> : null}
            {origin.message_ids?.length ? <p>{t("recall.materialMessages", { ids: origin.message_ids.join(", ") })}</p> : null}
          </div>
        ))}
        {(item.provenance.origin_count ?? 0) > origins.length && <p>
          {t("recall.moreOrigins", { count: item.provenance.origin_count })}
        </p>}
        {item.tags.length > 0 && <div className="flex flex-wrap gap-1">
          {item.tags.map((tag) => <span key={tag} className="rounded bg-surface px-1.5 py-0.5">{tag}</span>)}
        </div>}
      </div>}
    </details>
  );
}
