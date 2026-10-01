import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import axios from "axios";
import { ChevronDown, ChevronRight, Plus, RefreshCw, Server, Trash2 } from "lucide-react";
import { modelsApi, providersApi } from "@/lib/api";
import type { ProviderConfig } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button, EmptyState, toast } from "@/components/ui";
import { ProviderForm } from "./config/ProviderForm";
import { ProviderDetail } from "./config/ProviderDetail";

/** 模型配置面板：供应商列表（手风琴）+ 新建供应商 + 磁盘热重载 */
export function ConfigPanel() {
  const { t } = useTranslation("models");
  const qc = useQueryClient();
  const [expandedProvider, setExpandedProvider] = useState<string | null>(null);
  const [showNewProvider, setShowNewProvider] = useState(false);

  const { data: providers = [] } = useQuery<ProviderConfig[]>({
    queryKey: ["providers"],
    queryFn: () => providersApi.list().then((r) => r.data),
  });

  const removeProviderMut = useMutation({
    mutationFn: (pid: string) => providersApi.remove(pid),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["providers"] }); setExpandedProvider(null); },
  });

  const reloadMut = useMutation({
    mutationFn: () => modelsApi.reload().then((r) => r.data),
    onSuccess: (summary) => {
      qc.invalidateQueries({ queryKey: ["providers"] });
      const pa = summary.providers.added.length, pr = summary.providers.removed.length, pc = summary.providers.changed.length;
      const ma = summary.models.added.length, mr = summary.models.removed.length, mc = summary.models.changed.length;
      if (pa + pr + pc + ma + mr + mc === 0) {
        toast.info(t("reloadNoChange"));
      } else {
        toast.success(t("reloadResult", { pa, pr, pc, ma, mr, mc }));
      }
    },
    onError: (err) => {
      const detail = axios.isAxiosError(err) ? err.response?.data?.detail : null;
      toast.error(`${t("reloadFailed")}: ${typeof detail === "string" ? detail : String(err)}`);
    },
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-base font-semibold text-heading">{t("providersAndModels")}</h3>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            onClick={() => reloadMut.mutate()}
            disabled={reloadMut.isPending}
            title={t("reloadFromDiskHint")}
          >
            <RefreshCw size={16} className={reloadMut.isPending ? "animate-spin" : undefined} /> {t("reloadFromDisk")}
          </Button>
          <Button variant="primary" onClick={() => setShowNewProvider(!showNewProvider)}>
            <Plus size={16} /> {t("addProvider")}
          </Button>
        </div>
      </div>

      {showNewProvider && <ProviderForm onClose={() => setShowNewProvider(false)} />}

      <div className="grid gap-3">
        {providers.map((prov) => {
          const isOpen = expandedProvider === prov.id;
          return (
            <div
              key={prov.id}
              className={cn(
                "rounded-md border transition-all bg-card",
                isOpen ? "border-accent shadow-[0_0_0_2px_var(--bg),0_0_0_4px_var(--ring)]" : "border-border hover:border-border-strong",
              )}
            >
              <div
                className="flex items-center justify-between gap-2 p-3 md:p-4 cursor-pointer"
                onClick={() => setExpandedProvider(isOpen ? null : prov.id)}
              >
                <div className="flex items-center gap-2 md:gap-3 min-w-0">
                  {isOpen
                    ? <ChevronDown size={16} className="text-accent shrink-0" />
                    : <ChevronRight size={16} className="text-muted shrink-0" />}
                  <Server size={16} className="text-accent shrink-0" />
                  <span className="font-medium text-heading truncate">{prov.name || prov.id}</span>
                  <span className="text-xs text-muted shrink-0">{t("nModels", { count: prov.model_count })}</span>
                </div>
                <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                  <button
                    onClick={() => removeProviderMut.mutate(prov.id)}
                    className="p-1.5 rounded text-muted hover:text-danger transition-colors"
                    title={t("deleteProvider")}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>

              {isOpen && <ProviderDetail key={prov.id} provider={prov} />}
            </div>
          );
        })}
        {providers.length === 0 && !showNewProvider && (
          <EmptyState icon={Server} title={t("noProviders")} />
        )}
      </div>
    </div>
  );
}
