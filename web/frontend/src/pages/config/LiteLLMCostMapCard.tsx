import { useState, useEffect } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { modelsApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { cn } from "@/lib/utils";
import { Button, Input } from "@/components/ui";
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

  return (
    <Card title={t("costMap.title")} subtitle={info ? t("costMap.subtitle", { count: info.model_count }) : undefined}>
      <div className="space-y-3">
        <p className="text-xs text-muted">{t("costMap.description")}</p>
        <div className="flex items-center gap-2">
          <Input type="text" placeholder={t("costMap.proxyPlaceholder")} value={proxy} onChange={(e) => setProxy(e.target.value)} />
          <Button
            variant="primary"
            size="sm"
            onClick={() => updateMutation.mutate()}
            loading={updateMutation.isPending}
            className={cn("whitespace-nowrap shrink-0", resultCount !== null && "!bg-ok")}
          >
            {updateMutation.isPending ? <RefreshCw size={13} className="animate-spin" /> : resultCount !== null ? <Check size={13} /> : <RefreshCw size={13} />}
            {updateMutation.isPending ? t("costMap.updating") : resultCount !== null ? t("costMap.updated", { count: resultCount }) : t("costMap.updateNow")}
          </Button>
        </div>
        {error && (
          <div className="flex items-center gap-1.5 text-xs text-danger">
            <AlertCircle size={13} />
            <span>{error}</span>
          </div>
        )}
      </div>
    </Card>
  );
}
