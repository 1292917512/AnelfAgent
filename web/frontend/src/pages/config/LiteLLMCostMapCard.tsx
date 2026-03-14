import { useState, useEffect } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { modelsApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { cn } from "@/lib/utils";
import { Check, RefreshCw, AlertCircle } from "lucide-react";

export function LiteLLMCostMapCard({ defaultProxy }: { defaultProxy: string }) {
  const { t } = useTranslation("appconfig");
  const [proxy, setProxy] = useState(defaultProxy);
  const [error, setError] = useState("");
  const [resultCount, setResultCount] = useState<number | null>(null);

  useEffect(() => {
    setProxy(defaultProxy);
  }, [defaultProxy]);

  const { data: info } = useQuery({
    queryKey: ["costMapInfo"],
    queryFn: () => modelsApi.costMapInfo().then((r) => r.data),
  });

  const updateMutation = useMutation({
    mutationFn: () => modelsApi.updateCostMap(proxy),
    onSuccess: (r) => {
      setError("");
      setResultCount(r.data.model_count);
    },
    onError: (e: Error) => {
      setError(e.message || t("costMap.updateFailed"));
      setResultCount(null);
    },
  });

  const inputBase =
    "flex-1 text-sm bg-[var(--bg-elevated)] border border-[var(--border)] rounded-[var(--radius-md)] px-2.5 py-1.5 text-[var(--text-strong)] focus:outline-none focus:border-[var(--accent)] transition-colors";

  return (
    <Card title={t("costMap.title")} subtitle={info ? t("costMap.subtitle", { count: info.model_count }) : undefined}>
      <div className="space-y-3">
        <p className="text-xs text-[var(--muted)]">{t("costMap.description")}</p>
        <div className="flex items-center gap-2">
          <input type="text" className={inputBase} placeholder={t("costMap.proxyPlaceholder")} value={proxy} onChange={(e) => setProxy(e.target.value)} />
          <button
            onClick={() => updateMutation.mutate()}
            disabled={updateMutation.isPending}
            className={cn(
              "flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-[var(--radius-md)] border transition-all whitespace-nowrap",
              updateMutation.isPending
                ? "border-[var(--border)] bg-[var(--secondary)] text-[var(--muted)] cursor-not-allowed"
                : resultCount !== null
                  ? "border-[var(--ok)] bg-[var(--ok-subtle)] text-[var(--ok)]"
                  : "border-[var(--accent)] bg-[var(--accent)] text-white hover:opacity-90",
            )}
          >
            {updateMutation.isPending ? <RefreshCw size={13} className="animate-spin" /> : resultCount !== null ? <Check size={13} /> : <RefreshCw size={13} />}
            {updateMutation.isPending ? t("costMap.updating") : resultCount !== null ? t("costMap.updated", { count: resultCount }) : t("costMap.updateNow")}
          </button>
        </div>
        {error && (
          <div className="flex items-center gap-1.5 text-xs text-[var(--error)]">
            <AlertCircle size={13} />
            <span>{error}</span>
          </div>
        )}
      </div>
    </Card>
  );
}
