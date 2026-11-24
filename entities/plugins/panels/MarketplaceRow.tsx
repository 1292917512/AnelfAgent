import { RefreshCw, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Badge, Button, Switch } from "@/components/ui";
import { toast } from "@/stores/toast-store";
import { pluginsApi } from "./api";
import type { MarketplaceInfo } from "./types";
import { usePluginAction } from "./usePluginAction";

export function MarketplaceRow({ market, onChanged, onRemove }: {
  market: MarketplaceInfo; onChanged: () => Promise<void>; onRemove: () => void;
}) {
  const { t } = useTranslation("plugins");
  const { busy, run } = usePluginAction();
  const { name, enabled } = market;
  const refresh = () => run("refresh", async () => {
    const { data } = await pluginsApi.refreshMarketplaces(name);
    if ((data.results[name] ?? -1) >= 0) toast.success(t("refreshDone"));
    else toast.error(data.errors?.[name] || t("refreshFailed", { name }));
    await onChanged();
  });
  const toggle = (value: boolean) => run("toggle", async () => {
    await pluginsApi.toggleMarketplace(name, value);
    await onChanged();
  });
  return <li className="flex flex-wrap items-center gap-x-4 gap-y-2 py-3">
    <div className="min-w-0 flex-auto basis-56">
      <div className="flex flex-wrap items-center gap-2">
        <span className="break-all text-sm font-medium text-heading">{name}</span>
        <Badge variant="neutral">{t(market.builtin ? "builtin" : "custom")}</Badge>
        {market.plugin_count >= 0 ? <Badge variant="accent2">{t("pluginCount", { count: market.plugin_count })}</Badge>
          : <span className="text-xs text-muted">{t(market.pending ? "onDemand" : "catalogBroken")}</span>}
      </div>
      <p className="mt-1 break-all text-xs text-muted">{market.url || market.path}{market.ref && ` · ${market.ref}`}</p>
    </div>
    <div className="ml-auto flex items-center gap-2">
      {enabled && <Button size="sm" variant="ghost" loading={busy === "refresh"} disabled={!!busy} onClick={refresh}>
        <RefreshCw size={14} />{t("refresh")}</Button>}
      {!market.builtin && <Button size="sm" variant="ghost" disabled={!!busy} onClick={onRemove}>
        <Trash2 size={14} />{t("removeMarketplace")}</Button>}
      <Switch checked={enabled} label={t("sourceSwitch", { name })} onChange={toggle} disabled={!!busy} />
    </div>
  </li>;
}
