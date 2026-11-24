import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Store } from "lucide-react";
import { AsyncState } from "@/components/common/AsyncState";
import { MarketplaceRow } from "./MarketplaceRow";
import { usePluginAction } from "./usePluginAction";
import { NetworkConfigLink } from "@/components/common/NetworkSettings";
import { Card } from "@/components/common/Card";
import { Button, ConfirmDialog, EmptyState, Input, Modal } from "@/components/ui";
import { toast } from "@/stores/toast-store";
import { pluginsApi } from "./api";
import type { MarketplaceInfo } from "./types";

/** 管理持久化的市场来源以及目录刷新。 */
export function MarketplaceSection() {
  const { t } = useTranslation("plugins");
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [source, setSource] = useState("");
  const [ref, setRef] = useState("");
  const { busy, run } = usePluginAction();
  const [removing, setRemoving] = useState<MarketplaceInfo | null>(null);

  const marketQuery = useQuery({
    queryKey: ["plugins-marketplaces"],
    queryFn: () => pluginsApi.listMarketplaces().then((r) => r.data),
    throwOnError: false,
  });

  const marketplaces = marketQuery.data;
  const refreshAll = async () => {
    await queryClient.invalidateQueries({ queryKey: ["plugins-marketplaces"] });
    await queryClient.invalidateQueries({ queryKey: ["plugins-search"] });
  };

  const onAdd = async () => {
    if (!name.trim() || !source.trim()) return;
    await run("add", async () => {
      await pluginsApi.addMarketplace(name.trim(), source.trim(), ref.trim());
      toast.success(t("addMarketplace"));
      setAdding(false);
      setName("");
      setSource("");
      setRef("");
      await refreshAll();
    });
  };

  const onRemove = async () => {
    if (!removing) return;
    await run(`remove:${removing.name}`, async () => {
      await pluginsApi.removeMarketplace(removing.name);
      toast.success(t("removeMarketplace"));
      setRemoving(null);
      await refreshAll();
    });
  };

  return (
    <Card title={t("marketplaces")} subtitle={t("marketplacesDesc")} actions={<>
      <NetworkConfigLink />
      <Button variant="primary" onClick={() => setAdding(true)}><Plus size={15} />{t("addMarketplace")}</Button>
    </>}>
      <Modal open={adding} title={t("addMarketplace")} onClose={() => setAdding(false)} dismissible={!busy}>
        <form className="space-y-3" onSubmit={(event) => { event.preventDefault(); void onAdd(); }}>
          <label className="block space-y-1">
            <span className="text-sm font-medium">{t("marketplaceName")}</span>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t("marketplaceNamePlaceholder")}
              aria-label={t("marketplaceName")}
            />
          </label>
          <label className="block space-y-1">
            <span className="text-sm font-medium">{t("marketplaceSource")}</span>
            <Input
              value={source}
              onChange={(e) => setSource(e.target.value)}
              placeholder={t("marketplaceSourcePlaceholder")}
              aria-label={t("marketplaceSource")}
            />
          </label>
          <label className="block space-y-1">
            <span className="text-sm font-medium">{t("marketplaceRef")}</span>
            <Input
              value={ref}
              onChange={(e) => setRef(e.target.value)}
              placeholder={t("marketplaceRef")}
              aria-label={t("marketplaceRef")}
            />
          </label>
          <div className="flex justify-end">
            <Button
              size="sm"
              variant="primary"
              loading={busy === "add"}
              disabled={!!busy || !name.trim() || !source.trim()}
              type="submit"
            >
              <Plus size={14} /> {t("addFirst")}
            </Button>
          </div>
        </form>
      </Modal>

      <AsyncState pending={marketQuery.isPending} error={marketQuery.error} retry={() => void marketQuery.refetch()}>
        {!marketplaces?.length ? (
          <EmptyState icon={Store} title={t("noMarketplaces")} />
        ) : (
          <ul className="divide-y divide-border">
            {marketplaces.map((market) => <MarketplaceRow key={market.name} market={market}
              onChanged={refreshAll} onRemove={() => setRemoving(market)} />)}
          </ul>
        )}

      </AsyncState>
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
