import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowUpCircle, Package, Trash2 } from "lucide-react";
import { Card } from "@/components/common/Card";
import { Badge, Button, ConfirmDialog, EmptyState, Switch } from "@/components/ui";
import { toast } from "@/stores/toast-store";
import { pluginsApi } from "./api";
import type { InstalledPlugin } from "./types";

interface Props {
  plugins: InstalledPlugin[];
}

/** 错误摘要：优先取后端 detail 字段 */
function errText(exc: unknown, fallback: string): string {
  const detail = (exc as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
  return detail || (exc instanceof Error ? exc.message : fallback);
}

/** 已安装插件管理：升级 / 启停 / 移除，全部热生效 */
export function InstalledSection({ plugins }: Props) {
  const { t } = useTranslation("plugins");
  const queryClient = useQueryClient();
  const [removing, setRemoving] = useState<InstalledPlugin | null>(null);
  const [busy, setBusy] = useState("");

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["plugins-installed"] });

  const onToggle = async (p: InstalledPlugin, enabled: boolean) => {
    setBusy(`toggle:${p.name}`);
    try {
      await pluginsApi.toggle(p.name, enabled);
      toast.success(t("toggleDone", { name: p.name, state: t(enabled ? "enabled" : "disabled") }));
      await refresh();
    } catch (exc) {
      toast.error(errText(exc, t("operationFailed")));
    } finally {
      setBusy("");
    }
  };

  const onUpgrade = async (p: InstalledPlugin) => {
    setBusy(`upgrade:${p.name}`);
    try {
      const { data } = await pluginsApi.upgrade(p.name);
      toast.success(
        data.upgraded ? t("upgraded", { name: p.name, version: data.version }) : t("upToDate", { name: p.name }),
      );
      await refresh();
    } catch (exc) {
      toast.error(errText(exc, t("operationFailed")));
    } finally {
      setBusy("");
    }
  };

  const onUpgradeAll = async () => {
    setBusy("upgrade-all");
    try {
      const { data } = await pluginsApi.upgradeAll();
      const entries = Object.values(data.results);
      toast.success(t("upgradeAllDone", {
        changed: entries.filter(Boolean).length,
        total: entries.length,
      }));
      await refresh();
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
      await pluginsApi.remove(removing.name);
      toast.success(t("removeDone", { name: removing.name }));
      setRemoving(null);
      await refresh();
      await queryClient.invalidateQueries({ queryKey: ["plugins-search"] });
    } catch (exc) {
      toast.error(errText(exc, t("operationFailed")));
    } finally {
      setBusy("");
    }
  };

  return (
    <Card
      title={t("installed")}
      subtitle={t("installedDesc")}
      actions={
        plugins.length > 0 ? (
          <Button size="sm" variant="secondary" loading={busy === "upgrade-all"} onClick={onUpgradeAll}>
            <ArrowUpCircle size={14} /> {t("upgradeAll")}
          </Button>
        ) : undefined
      }
    >
      {plugins.length === 0 ? (
        <EmptyState icon={Package} title={t("noPlugins")} />
      ) : (
        <ul className="divide-y divide-border">
          {plugins.map((p) => (
            <li key={p.name} className="py-3 flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm font-medium text-foreground">{p.display_name || p.name}</span>
                  <Badge variant="accent">v{p.version || "?"}</Badge>
                  <Badge variant={p.enabled ? "ok" : "neutral"}>
                    {t(p.enabled ? "enabled" : "disabled")}
                  </Badge>
                  {p.marketplace && <Badge variant="info">{p.marketplace}</Badge>}
                </div>
                {p.description && <p className="mt-1 text-xs text-muted">{p.description}</p>}
                <p className="mt-1 text-xs text-muted">
                  {t("components")}: {t("skills")} {p.skills.length} · {t("tools")} {p.tools.length} ·{" "}
                  {t("mcpServers")} {p.mcp_servers.length}
                </p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <Switch
                  checked={p.enabled}
                  disabled={busy === `toggle:${p.name}`}
                  onChange={(v) => onToggle(p, v)}
                />
                <Button
                  size="sm"
                  variant="ghost"
                  loading={busy === `upgrade:${p.name}`}
                  onClick={() => onUpgrade(p)}
                >
                  <ArrowUpCircle size={14} /> {t("upgrade")}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setRemoving(p)}>
                  <Trash2 size={14} /> {t("remove")}
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
        title={t("remove")}
        message={t("removeConfirm", { name: removing?.name ?? "" })}
        danger
        loading={busy.startsWith("remove:")}
      />
    </Card>
  );
}
