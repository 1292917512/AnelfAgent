import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronRight, Plus, Server, Trash2 } from "lucide-react";
import { providersApi } from "@/lib/api";
import type { ProviderConfig } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button, EmptyState } from "@/components/ui";
import { ProviderForm } from "./config/ProviderForm";
import { ProviderDetail } from "./config/ProviderDetail";

/** 模型配置面板：供应商列表（手风琴）+ 新建供应商 */
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

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-base font-semibold text-heading">{t("providersAndModels")}</h3>
        <Button variant="primary" onClick={() => setShowNewProvider(!showNewProvider)}>
          <Plus size={16} /> {t("addProvider")}
        </Button>
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
