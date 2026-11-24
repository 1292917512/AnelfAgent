import { useDeferredValue, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, SearchX, Store } from "lucide-react";
import { AsyncState } from "@/components/common/AsyncState";
import { usePluginAction } from "./usePluginAction";
import { PluginCard } from "./PluginCard";
import { NetworkConfigLink } from "@/components/common/NetworkSettings";
import { Card } from "@/components/common/Card";
import { Badge, Button, EmptyState, Input, Modal, Select } from "@/components/ui";
import { toast } from "@/stores/toast-store";
import { pluginsApi, refreshPluginViews } from "./api";
import type { MarketplacePluginEntry } from "./types";

/** 浏览与安装：跨市场检索 + 直接来源安装 */
export function BrowseSection({ onViewMarkets }: { onViewMarkets: () => void }) {
  const { t } = useTranslation("plugins");
  const queryClient = useQueryClient();
  const [selection, setSelection] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [showSource, setShowSource] = useState(false);
  const [selected, setSelected] = useState<MarketplacePluginEntry | null>(null);
  const markets = useQuery({
    queryKey: ["plugins-marketplaces"], queryFn: () => pluginsApi.listMarketplaces().then((r) => r.data), throwOnError: false,
  });
  const [query, setQuery] = useState("");
  const keyword = useDeferredValue(query.trim());
  const [source, setSource] = useState("");
  const [ref, setRef] = useState("");
  const [subdir, setSubdir] = useState("");
  const { busy, run } = usePluginAction();

  const active = markets.data?.filter((market) => market.enabled !== false) ?? [];
  const hasMarkets = active.length > 0;
  const marketplace = selection === "" || active.some((market) => market.name === selection)
    ? selection ?? "" : active[0]?.name ?? "";
  const pageSize = 36;
  const search = useQuery({
    enabled: hasMarkets,
    queryKey: ["plugins-search", keyword, marketplace, offset],
    queryFn: ({ signal }) => pluginsApi.search(keyword, marketplace, offset, pageSize, signal).then((r) => r.data),
    retry: false,
    throwOnError: false,
  });

  const result = hasMarkets ? search.data : undefined;

  const retry = async () => {
    if (markets.error) {
      await markets.refetch();
    } else if (hasMarkets) {
      if (marketplace && search.error) {
        await run("refresh", () => pluginsApi.refreshMarketplaces(marketplace).then(() => undefined));
      }
      await search.refetch();
    }
  };

  const onInstall = async (entry: MarketplacePluginEntry) => {
    await run(`install:${entry.marketplace}:${entry.name}`, async () => {
      await pluginsApi.install(entry.name, entry.marketplace);
      setSelected(null);
      toast.success(t("installDone", { name: entry.name }));
      await refreshPluginViews(queryClient);
    });
  };

  const onInstallFromSource = async () => {
    if (!source.trim()) return;
    await run("install-source", async () => {
      const { data } = await pluginsApi.installFromSource(source.trim(), ref.trim(), subdir.trim());
      toast.success(t("installDone", { name: data.name }));
      setShowSource(false);
      setSource("");
      setRef("");
      setSubdir("");
      await refreshPluginViews(queryClient);
    });
  };

  return (
    <Card title={t("browse")} actions={<Button variant="primary" onClick={() => setShowSource(true)}>
      <Download size={15} />{t("installFromSource")}</Button>}>
      {hasMarkets && <div className="mb-4 grid gap-2 sm:grid-cols-[minmax(10rem,14rem)_1fr]">
        <Select aria-label={t("marketplaces")} value={marketplace} onChange={(e) => { setSelection(e.target.value); setOffset(0); }}>
          {active.map((market) => <option key={market.name} value={market.name}>{market.name}</option>)}
          <option value="">{t("allMarkets")}</option>
        </Select>
        <Input value={query} onChange={(e) => { setQuery(e.target.value); setOffset(0); }} aria-label={t("search")} placeholder={t("searchPlaceholder")} />
      </div>}
      <AsyncState pending={markets.isPending || (hasMarkets && search.isPending)} error={markets.error || (hasMarkets && search.error)}
        retry={() => void retry()}>
        {result?.errors && Object.keys(result.errors).length > 0 && <p role="status" className="mb-3 text-sm text-muted">
          {t("partialSources", { names: Object.keys(result.errors).join(", ") })}</p>}
        {!hasMarkets ? <EmptyState icon={Store} title={t(markets.data?.length ? "noActiveSources" : "noMarketplaces")}
          action={<Button onClick={onViewMarkets}>{t("viewMarkets")}</Button>} /> :
          !result?.plugins.length ? <EmptyState icon={SearchX} title={t("noResults")} /> :
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {result.plugins.map((entry) => <PluginCard key={`${entry.marketplace}:${entry.name}`}
              title={entry.name} description={entry.description} meta={[entry.marketplace, entry.category].filter(Boolean).join(" · ")}
              status={entry.installed && <Badge variant="ok">{t("installedBadge")}</Badge>} onOpen={() => setSelected(entry)} />)}
          </div>}
        {result && (offset > 0 || result.next_offset != null) && <nav className="mt-4 flex flex-wrap items-center justify-end gap-2" aria-label={t("pagination")}>
          {result.plugins.length > 0 && <span className="mr-auto text-xs text-muted">
            {t("range", { start: offset + 1, end: offset + result.plugins.length, total: result.count })}</span>}
          <Button size="sm" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - pageSize))}>{t("prev")}</Button>
          <Button size="sm" disabled={result.next_offset == null} onClick={() => setOffset(result.next_offset ?? offset)}>{t("next")}</Button>
        </nav>}
      </AsyncState>
      <Modal open={!!selected} onClose={() => setSelected(null)} dismissible={!busy} title={selected?.name} placement="right"
        footer={selected && <Button variant="primary" loading={!!busy} disabled={selected.installed || selected.installation === "NOT_AVAILABLE"}
          onClick={() => onInstall(selected)}>{t(selected.installed ? "installedBadge" : selected.installation === "NOT_AVAILABLE" ? "unavailable" : "install")}</Button>}>
        {selected && <div className="space-y-4 text-sm">
          <Badge variant="neutral">{selected.marketplace}</Badge>
          {markets.data?.find((m) => m.name === selected.marketplace)?.source_type === "github-topic" && <p className="text-xs text-muted">{t("mdTopicHint")}</p>}
          {selected.description && <p>{selected.description}</p>}
          <p className="break-all text-muted">{[selected.url || selected.path, selected.subdir, selected.ref].filter(Boolean).join(" · ")}</p>
        </div>}
      </Modal>
      <Modal open={showSource} onClose={() => setShowSource(false)} dismissible={!busy} title={t("installFromSource")}>
        <form className="space-y-3" onSubmit={(event) => { event.preventDefault(); void onInstallFromSource(); }}>
          <p className="text-xs leading-relaxed text-muted">{t("sourceHint")}{" "}
            <a className="text-accent underline" href="https://github.com/topics/dsh-plugin" target="_blank" rel="noreferrer">{t("dshDirectory")}</a>
          </p>
          <label className="block space-y-1">
            <span className="text-sm font-medium">{t("sourceLabel")}</span>
            <Input
              value={source}
              onChange={(e) => setSource(e.target.value)}
              placeholder={t("sourcePlaceholder")}
              required aria-label={t("sourceLabel")}
            />
          </label>
          <label className="block space-y-1">
            <span className="text-sm font-medium">{t("refLabel")}</span>
            <Input
              value={ref}
              onChange={(e) => setRef(e.target.value)}
              placeholder={t("refLabel")}
              aria-label={t("refLabel")}
            />
          </label>
          <label className="block space-y-1">
            <span className="text-sm font-medium">{t("subdirLabel")}</span>
            <Input
              value={subdir}
              onChange={(e) => setSubdir(e.target.value)}
              placeholder={t("subdirPlaceholder")}
              aria-label={t("subdirLabel")}
            />
          </label>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <NetworkConfigLink />
            <Button type="submit" variant="primary" loading={!!busy} disabled={!source.trim()}><Download size={14} />{t("install")}</Button>
          </div>
        </form>
      </Modal>
    </Card>
  );
}
