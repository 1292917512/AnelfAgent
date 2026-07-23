import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { RefreshCw, ExternalLink, Waypoints } from "lucide-react";
import { Card } from "@/components/common/Card";
import type { CogneeDataset } from "@/lib/types";

export function CogneeGraphCard({
  ready,
  datasets,
}: {
  ready: boolean;
  datasets?: CogneeDataset[];
}) {
  const { t } = useTranslation("memory");
  const [selected, setSelected] = useState("");
  const [nonce, setNonce] = useState(0);
  const [loading, setLoading] = useState(true);

  const names = useMemo(
    () => (datasets || []).map((ds) => ds.name).filter(Boolean),
    [datasets],
  );
  // 访问控制开启时 cognee 强制要求指定数据集，空选择回退到首个数据集
  const dataset = selected || names[0] || "";
  const hasDataset = Boolean(dataset);

  const graphUrl = useMemo(() => {
    const params = new URLSearchParams();
    if (dataset) params.set("dataset", dataset);
    params.set("ts", String(nonce));
    return `/api/memory/cognee/graph?${params.toString()}`;
  }, [dataset, nonce]);

  return (
    <Card
      title={t("cognee.graphTitle")}
      subtitle={t("cognee.graphSubtitle")}
      actions={
        ready && hasDataset ? (
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-1.5 text-xs text-muted">
              {t("cognee.graphDataset")}
              <select
                value={dataset}
                onChange={(e) => {
                  setSelected(e.target.value);
                  setLoading(true);
                }}
                className="px-2 py-1 text-xs rounded-md border border-border bg-elevated text-heading"
              >
                {names.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
            <button
              onClick={() => {
                setNonce(Date.now());
                setLoading(true);
              }}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-elevated text-muted hover:bg-hover transition-all"
            >
              <RefreshCw size={14} /> {t("cognee.graphRefresh")}
            </button>
            <a
              href={graphUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-elevated text-muted hover:bg-hover transition-all"
            >
              <ExternalLink size={14} /> {t("cognee.graphOpenNew")}
            </a>
          </div>
        ) : undefined
      }
    >
      {ready && hasDataset ? (
        <div className="relative rounded-md border border-border overflow-hidden bg-white">
          {loading && (
            <div className="absolute inset-0 z-10 flex items-center justify-center gap-2 text-sm text-muted bg-bg/80">
              <Waypoints size={16} className="animate-pulse" />
              {t("cognee.graphLoading")}
            </div>
          )}
          <iframe
            key={graphUrl}
            src={graphUrl}
            onLoad={() => setLoading(false)}
            className="w-full border-0 h-[70dvh]"
            sandbox="allow-scripts allow-same-origin"
            title={t("cognee.graphTitle")}
          />
        </div>
      ) : (
        <p className="text-sm text-muted">
          {ready ? t("cognee.noDatasets") : t("cognee.graphNotReady")}
        </p>
      )}
    </Card>
  );
}
