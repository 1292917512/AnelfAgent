import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowUpRight, Check, CircleAlert } from "lucide-react";
import { Link } from "react-router-dom";
import type { DesktopModuleInfo } from "./types";

export function NetworkDetailView({ mod }: { mod: DesktopModuleInfo }) {
  const { t } = useTranslation("ai_desktop");
  const [expanded, setExpanded] = useState(false);
  const status = mod.detail.network;
  if (!status) return null;
  const rows = [...status.observations].sort((a, b) => Number(b.state === "failed") - Number(a.state === "failed") || b.observed_at - a.observed_at);
  return <div className="space-y-2 text-xs">
    <div className="flex flex-wrap items-center gap-2 text-muted">
      <span>{t(status.enabled ? "network.enabled" : "network.disabled")}</span>
      <Link to="/config?group=network%2Fproxy" className="ml-auto inline-flex min-h-9 items-center gap-1 text-accent">{t("network.settings")}<ArrowUpRight size={13} /></Link>
    </div>
    {status.proxies.map((proxy) => <p key={proxy} className="break-all font-mono text-muted">{proxy}</p>)}
    <p className="text-muted">{t("network.hint")}</p>
    {!rows.length && <p className="py-2 text-muted">{t("network.unknown")}</p>}
    {(expanded ? rows : rows.slice(0, 4)).map((row) => <div key={`${row.proxy}:${row.target}`} className="flex items-start gap-2 rounded-md border border-border/60 px-3 py-2">
      {row.state === "failed" ? <CircleAlert size={14} className="mt-0.5 shrink-0 text-warn" /> : <Check size={14} className="mt-0.5 shrink-0 text-ok" />}
      <div className="min-w-0 flex-1 space-y-1">
        <p className="break-all font-medium">{row.target}</p>
        <p className="break-all text-muted">{t(`network.${row.state}`)}{row.error_code && ` · ${row.error_code}`}{row.status_code != null && ` · HTTP ${row.status_code}`}</p>
        <p className="break-all text-muted">{row.proxy}</p>
      </div>
      <time className="shrink-0 text-muted">{new Date(row.observed_at * 1000).toLocaleTimeString()}</time>
    </div>)}
    {rows.length > 4 && <button className="min-h-9 text-accent" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{t(expanded ? "network.less" : "network.more", { count: rows.length - 4 })}</button>}
  </div>;
}
