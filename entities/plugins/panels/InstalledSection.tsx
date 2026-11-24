import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpCircle, Package, Trash2 } from "lucide-react";
import { AsyncState } from "@/components/common/AsyncState";
import { usePluginAction } from "./usePluginAction";
import { PluginCard } from "./PluginCard";
import { Card } from "@/components/common/Card";
import { Badge, Button, ConfirmDialog, EmptyState, Modal, Switch } from "@/components/ui";
import { toast } from "@/stores/toast-store";
import { pluginsApi, refreshPluginViews } from "./api";
import type { InstalledPlugin } from "./types";

/** 已安装插件管理：升级 / 启停 / 移除，全部热生效 */
export function InstalledSection({ onBrowse }: { onBrowse: () => void }) {
  const { t } = useTranslation("plugins");
  const queryClient = useQueryClient();
  const [removing, setRemoving] = useState<InstalledPlugin | null>(null);
  const [selectedName, setSelectedName] = useState("");
  const { busy, run } = usePluginAction();

  const installed = useQuery({
    queryKey: ["plugins-installed"],
    queryFn: () => pluginsApi.list().then((response) => response.data),
    throwOnError: false,
  });
  const plugins = installed.data ?? [];
  const selected = plugins.find((plugin) => plugin.name === selectedName);
  const refresh = () => refreshPluginViews(queryClient);

  const onToggle = async (p: InstalledPlugin, enabled: boolean) => {
    await run(`toggle:${p.name}`, async () => {
      await pluginsApi.toggle(p.name, enabled);
      toast.success(t("toggleDone", { name: p.name, state: t(enabled ? "enabled" : "disabled") }));
      await refresh();
    });
  };

  const onUpgrade = async (p: InstalledPlugin) => {
    await run(`upgrade:${p.name}`, async () => {
      const { data } = await pluginsApi.upgrade(p.name);
      toast.success(
        data.upgraded ? t("upgraded", { name: p.name, version: data.version }) : t("upToDate", { name: p.name }),
      );
      await refresh();
    });
  };

  const onUpgradeAll = async () => {
    await run("upgrade-all", async () => {
      const { data } = await pluginsApi.upgradeAll();
      const entries = Object.values(data.results);
      const failures = Object.keys(data.errors ?? {});
      if (failures.length) {
        toast.error(t("upgradeFailures", { names: failures.join(", ") }));
      } else toast.success(t("upgradeAllDone", {
        changed: entries.filter(Boolean).length,
        total: entries.length,
      }));
      await refresh();
    });
  };

  const onRemove = async () => {
    if (!removing) return;
    await run(`remove:${removing.name}`, async () => {
      await pluginsApi.remove(removing.name);
      toast.success(t("removeDone", { name: removing.name }));
      setRemoving(null);
      setSelectedName("");
      await refresh();
    });
  };

  return (
    <Card
      title={t("installed")}
      subtitle={plugins.length ? t("installedDesc") : undefined}
      actions={
        plugins.length > 0 ? (
          <Button size="sm" variant="secondary" disabled={!!busy} loading={busy === "upgrade-all"} onClick={onUpgradeAll}>
            <ArrowUpCircle size={14} /> {t("upgradeAll")}
          </Button>
        ) : undefined
      }
    >
      <AsyncState pending={installed.isPending} error={installed.error} retry={() => void installed.refetch()}>
        {plugins.length === 0 ? (
          <EmptyState icon={Package} title={t("noPlugins")}
            action={<Button variant="primary" onClick={onBrowse}>{t("browse")}</Button>} />
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {plugins.map((plugin) => <PluginCard key={plugin.name} title={plugin.display_name || plugin.name}
              description={plugin.description} onOpen={() => setSelectedName(plugin.name)}
              status={<Badge variant={plugin.enabled ? "ok" : "neutral"}>{t(plugin.enabled ? "enabled" : "disabled")}</Badge>}
              meta={[plugin.format?.startsWith("dsh") ? "DSH" : "", plugin.version, plugin.marketplace || t("directSource")].filter(Boolean).join(" · ")} />)}
          </div>
        )}
      </AsyncState>
      <Modal open={!!selected && !removing} onClose={() => setSelectedName("")} placement="right"
        dismissible={!busy} title={selected?.display_name || selected?.name}
        footer={selected && <>
          <Button variant="ghost" disabled={!!busy} onClick={() => setRemoving(selected)}><Trash2 size={15} />{t("remove")}</Button>
          <Button loading={busy.startsWith("upgrade:")} disabled={!!busy} onClick={() => onUpgrade(selected)}><ArrowUpCircle size={15} />{t("upgrade")}</Button>
        </>}>
        {selected && <div className="space-y-5">
          {selected.description && <p className="text-sm text-muted">{selected.description}</p>}
          <div className="flex items-center justify-between">
            <span className="text-sm">{t(selected.enabled ? "enabled" : "disabled")}</span>
            <Switch checked={selected.enabled} disabled={!!busy} label={t("toggleLabel", { name: selected.name })}
              onChange={(value) => onToggle(selected, value)} />
          </div>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-3 text-sm">
            <dt>{t("from")}</dt><dd className="break-all text-muted">{selected.source}</dd>
            {selected.subdir && <><dt>{t("directory")}</dt><dd className="break-all">{selected.subdir}</dd></>}
            <dt>{t("format")}</dt><dd>{t(selected.format === "dsh-host" ? "formatHost" : selected.format === "dsh" ? "formatMcp" : "formatNative")}</dd>
            {selected.version && <><dt>{t("version")}</dt><dd>{selected.version}</dd></>}
            {selected.dsh_home && <><dt>DSH_HOME</dt><dd className="break-all font-mono">{selected.dsh_home}</dd></>}
          </dl>
          {selected.dsh_home && <p className="text-xs text-muted">{t("hostInstructions")}</p>}
          {(["skills", "tools", "mcp_servers"] as const).map((key) => selected[key].length > 0 && <div key={key}>
            <h3 className="mb-2 text-sm font-medium">{t(key === "mcp_servers" ? "mcpServers" : key)}</h3>
            <div className="flex flex-wrap gap-2">{selected[key].map((name) => <Badge key={name} variant="neutral">{name}</Badge>)}</div>
          </div>)}
        </div>}
      </Modal>
      <ConfirmDialog
        open={removing !== null}
        onClose={() => setRemoving(null)}
        onConfirm={onRemove}
        title={t("remove")}
        message={t(removing?.format === "dsh-host" ? "removeHostConfirm" : "removeConfirm", { name: removing?.name ?? "" })}
        danger
        loading={busy.startsWith("remove:")}
      />
    </Card>
  );
}
