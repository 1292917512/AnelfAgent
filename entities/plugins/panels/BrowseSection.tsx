import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Search, SearchX } from "lucide-react";
import { Card } from "@/components/common/Card";
import { Badge, Button, EmptyState, Input } from "@/components/ui";
import { toast } from "@/stores/toast-store";
import { pluginsApi } from "./api";
import type { MarketplacePluginEntry } from "./types";

function errText(exc: unknown, fallback: string): string {
  const detail = (exc as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
  return detail || (exc instanceof Error ? exc.message : fallback);
}

/** 浏览与安装：跨市场检索 + 直接来源安装 */
export function BrowseSection() {
  const { t } = useTranslation("plugins");
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const [source, setSource] = useState("");
  const [ref, setRef] = useState("");
  const [subdir, setSubdir] = useState("");
  const [busy, setBusy] = useState("");

  const { data: result } = useQuery({
    queryKey: ["plugins-search", query],
    queryFn: () => pluginsApi.search(query).then((r) => r.data),
  });

  const afterInstall = async () => {
    await queryClient.invalidateQueries({ queryKey: ["plugins-installed"] });
    await queryClient.invalidateQueries({ queryKey: ["plugins-search"] });
  };

  const onInstall = async (entry: MarketplacePluginEntry) => {
    setBusy(`install:${entry.marketplace}:${entry.name}`);
    try {
      await pluginsApi.install(entry.name, entry.marketplace);
      toast.success(t("installDone", { name: entry.name }));
      await afterInstall();
    } catch (exc) {
      toast.error(errText(exc, t("operationFailed")));
    } finally {
      setBusy("");
    }
  };

  const onInstallFromSource = async () => {
    if (!source.trim()) return;
    setBusy("install-source");
    try {
      const { data } = await pluginsApi.installFromSource(source.trim(), ref.trim(), subdir.trim());
      toast.success(t("installDone", { name: data.name }));
      setSource("");
      setRef("");
      setSubdir("");
      await afterInstall();
    } catch (exc) {
      toast.error(errText(exc, t("operationFailed")));
    } finally {
      setBusy("");
    }
  };

  return (
    <Card title={t("browse")} subtitle={t("browseDesc")}>
      <div className="flex gap-2 mb-4">
        <div className="flex-1">
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("searchPlaceholder")}
            aria-label={t("search")}
          />
        </div>
        <Button size="sm" variant="secondary" onClick={() => setQuery(query.trim())}>
          <Search size={14} /> {t("search")}
        </Button>
      </div>

      {!result?.plugins.length ? (
        <EmptyState icon={SearchX} title={t("noResults")} />
      ) : (
        <ul className="divide-y divide-border mb-4">
          {result.plugins.map((entry) => (
            <li key={`${entry.marketplace}:${entry.name}`} className="py-3 flex items-center gap-3">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm font-medium text-foreground">{entry.name}</span>
                  {entry.category && <Badge variant="accent2">{entry.category}</Badge>}
                  <Badge variant="neutral">{entry.marketplace}</Badge>
                  {entry.installed && (
                    <Badge variant="ok">{t("installedBadge")} {entry.installed_version}</Badge>
                  )}
                </div>
                {entry.description && (
                  <p className="mt-1 text-xs text-muted">{entry.description}</p>
                )}
              </div>
              {!entry.installed && (
                <Button
                  size="sm"
                  variant="primary"
                  loading={busy === `install:${entry.marketplace}:${entry.name}`}
                  onClick={() => onInstall(entry)}
                >
                  <Download size={14} /> {t("install")}
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}

      <div className="border-t border-border pt-4">
        <p className="text-xs font-medium text-foreground mb-2">{t("installFromSource")}</p>
        <div className="grid grid-cols-1 sm:grid-cols-[2fr_1fr_1fr_auto] gap-2">
          <Input
            value={source}
            onChange={(e) => setSource(e.target.value)}
            placeholder={t("sourcePlaceholder")}
            aria-label={t("sourceLabel")}
          />
          <Input
            value={ref}
            onChange={(e) => setRef(e.target.value)}
            placeholder={t("refLabel")}
            aria-label={t("refLabel")}
          />
          <Input
            value={subdir}
            onChange={(e) => setSubdir(e.target.value)}
            placeholder={t("subdirPlaceholder")}
            aria-label={t("subdirLabel")}
          />
          <Button
            size="sm"
            variant="primary"
            loading={busy === "install-source"}
            disabled={!source.trim()}
            onClick={onInstallFromSource}
          >
            <Download size={14} /> {t("install")}
          </Button>
        </div>
      </div>
    </Card>
  );
}
