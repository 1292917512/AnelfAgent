import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, RefreshCw, Store, Trash2 } from "lucide-react";
import { Card } from "@/components/common/Card";
import { Badge, Button, ConfirmDialog, EmptyState, Input } from "@/components/ui";
import { toast } from "@/stores/toast-store";
import { pluginsApi } from "./api";
import type { MarketplaceInfo } from "./types";

function errText(exc: unknown, fallback: string): string {
  const detail = (exc as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
  return detail || (exc instanceof Error ? exc.message : fallback);
}

/** 市场订阅管理：添加（本地路径 / git 仓库）、刷新目录、退订 */
export function MarketplaceSection() {
  const { t } = useTranslation("plugins");
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [source, setSource] = useState("");
  const [ref, setRef] = useState("");
  const [busy, setBusy] = useState("");
  const [removing, setRemoving] = useState<MarketplaceInfo | null>(null);

  const { data: marketplaces } = useQuery({
    queryKey: ["plugins-marketplaces"],
    queryFn: () => pluginsApi.listMarketplaces().then((r) => r.data),
  });

  const refreshAll = async () => {
    await queryClient.invalidateQueries({ queryKey: ["plugins-marketplaces"] });
    await queryClient.invalidateQueries({ queryKey: ["plugins-search"] });
  };

  const onAdd = async () => {
    if (!name.trim() || !source.trim()) return;
    setBusy("add");
    try {
      await pluginsApi.addMarketplace(name.trim(), source.trim(), ref.trim());
      toast.success(t("addMarketplace"));
      setName("");
      setSource("");
      setRef("");
      await refreshAll();
    } catch (exc) {
      toast.error(errText(exc, t("operationFailed")));
    } finally {
      setBusy("");
    }
  };

  const onRefresh = async (marketName: string) => {
    setBusy(`refresh:${marketName}`);
    try {
      await pluginsApi.refreshMarketplaces(marketName);
      toast.success(t("refreshDone"));
      await refreshAll();
    } catch (exc) {
      toast.error(errText(exc, t("operationFailed")));
    } finally {
      setBusy("");
    }
  };

  const onRemove = async () => {
    if (!removing) return;
    setBusy(`remove:${removing.name}`);
    try {
      await pluginsApi.removeMarketplace(removing.name);
      toast.success(t("removeMarketplace"));
      setRemoving(null);
      await refreshAll();
    } catch (exc) {
      toast.error(errText(exc, t("operationFailed")));
    } finally {
      setBusy("");
    }
  };

  return (
    <Card title={t("marketplaces")} subtitle={t("marketplacesDesc")}>
      <div className="grid grid-cols-1 sm:grid-cols-[1fr_2fr_1fr_auto] gap-2 mb-4">
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t("marketplaceNamePlaceholder")}
          aria-label={t("marketplaceName")}
        />
        <Input
          value={source}
          onChange={(e) => setSource(e.target.value)}
          placeholder={t("marketplaceSourcePlaceholder")}
          aria-label={t("marketplaceSource")}
        />
        <Input
          value={ref}
          onChange={(e) => setRef(e.target.value)}
          placeholder={t("marketplaceRef")}
          aria-label={t("marketplaceRef")}
        />
        <Button
          size="sm"
          variant="primary"
          loading={busy === "add"}
          disabled={!name.trim() || !source.trim()}
          onClick={onAdd}
        >
          <Plus size={14} /> {t("addFirst")}
        </Button>
      </div>

      {!marketplaces?.length ? (
        <EmptyState icon={Store} title={t("noMarketplaces")} />
      ) : (
        <ul className="divide-y divide-border">
          {marketplaces.map((m) => (
            <li key={m.name} className="py-3 flex items-center gap-3">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm font-medium text-foreground">{m.name}</span>
                  <Badge variant="neutral">{m.source_type}</Badge>
                  {m.plugin_count >= 0 ? (
                    <Badge variant="accent2">{t("pluginCount", { count: m.plugin_count })}</Badge>
                  ) : (
                    <Badge variant="danger">{t("catalogBroken")}</Badge>
                  )}
                </div>
                <p className="mt-1 text-xs text-muted truncate">{m.url || m.path}</p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <Button
                  size="sm"
                  variant="ghost"
                  loading={busy === `refresh:${m.name}`}
                  onClick={() => onRefresh(m.name)}
                >
                  <RefreshCw size={14} /> {t("refresh")}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setRemoving(m)}>
                  <Trash2 size={14} /> {t("removeMarketplace")}
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <ConfirmDialog
        open={removing !== null}
        onClose={() => setRemoving(null)}
        onConfirm={onRemove}
        title={t("removeMarketplace")}
        message={t("removeMarketplaceConfirm", { name: removing?.name ?? "" })}
        danger
        loading={busy.startsWith("remove:")}
      />
    </Card>
  );
}
