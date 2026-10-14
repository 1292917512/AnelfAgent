import { lazy, Suspense, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ArrowRight, Globe2 } from "lucide-react";
import { apiErrorMessage, retrievalApi } from "@/lib/api";
import { AsyncState, QueryError } from "@/components/common/AsyncState";
import { Badge, Button, Modal, Select } from "@/components/ui";
import { RetrievalProviderCard } from "./ProviderCard";

const DeploymentPanel = lazy(() => import("./DeploymentPanel").then((module) => ({ default: module.RetrievalDeploymentPanel })));

export function RetrievalProvidersPanel() {
  const { t } = useTranslation("retrieval");
  const [deploymentOpen, setDeploymentOpen] = useState(false);
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ["retrievalProvidersMatrix"], queryFn: () => retrievalApi.matrix().then((r) => r.data), throwOnError: false,
  });
  const invalidate = () => client.invalidateQueries({ queryKey: ["retrievalProvidersMatrix"] });
  const active = useMutation({
    mutationFn: ({ capability, provider }: { capability: string; provider: string }) => retrievalApi.setActive(capability, provider),
    onSuccess: invalidate,
  });
  const data = query.data;
  return <AsyncState pending={query.isPending} error={!data && query.error} retry={() => void query.refetch()}>
    {data && <div className="space-y-6">
      {query.isError && <QueryError compact error={query.error} retry={() => void query.refetch()} />}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {data.capabilities.map((cap) => {
          const capable = data.providers.filter((p) => p.capabilities.includes(cap));
          const selection = data.selection[cap] || "auto";
          const current = data.providers.find((p) => p.name === data.active[cap]);
          return <section key={cap} className="min-w-0 rounded-xl border border-border bg-card p-4">
            <div className="mb-4 flex items-center gap-2 text-heading"><Globe2 size={16} className="text-accent" /><h2 className="text-sm font-semibold">{t(`caps.${cap}`)}</h2></div>
            <Select className="w-full" aria-label={t(`caps.${cap}`)} value={selection} disabled={active.isPending}
              onChange={(event) => active.mutate({ capability: cap, provider: event.target.value })}>
              <option value="auto">{t("matrix.auto")}</option>
              {!capable.some((p) => p.name === selection) && selection !== "auto" && <option value={selection} disabled>{selection} · {t("matrix.missing")}</option>}
              {capable.map((p) => <option key={p.name} value={p.name} disabled={!p.enabled || !p.configured}>
                {p.display_name}{!p.enabled ? ` · ${t("provider.disabledBadge")}` : !p.configured ? ` · ${t("provider.notConfigured")}` : ""}
              </option>)}
            </Select>
            <div className="mt-3 flex items-center gap-2 text-xs text-muted"><ArrowRight size={13} className="shrink-0" />
              <span className="min-w-0 break-words">{current ? current.display_name : t("matrix.noneAvailable")}</span>
              {current && <Badge variant="ok" className="ml-auto shrink-0">{t("matrix.activeNow")}</Badge>}
            </div>
          </section>;
        })}
      </div>
      {active.isError && <p role="alert" className="text-sm text-danger">{apiErrorMessage(active.error, t("common:requestFailed"))}</p>}
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div><h2 className="text-sm font-semibold text-heading">{t("provider.title")}</h2><p className="mt-1 text-xs text-muted">{t("matrix.autoDesc")}</p></div>
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-xs tabular-nums text-muted">{t("provider.count", { ready: data.providers.filter((p) => p.enabled && p.configured).length, total: data.providers.length })}</span>
          <Button size="sm" onClick={() => setDeploymentOpen(true)}>{t("deployment.title")}</Button>
        </div>
      </div>
      <div className="grid items-start gap-4 xl:grid-cols-2">
        {data.providers.map((provider) => <RetrievalProviderCard key={provider.name} provider={provider} onChanged={invalidate} />)}
      </div>
      <Modal open={deploymentOpen} onClose={() => { setDeploymentOpen(false); void invalidate(); }} title={t("deployment.title")} width="max-w-2xl">
        {deploymentOpen && <Suspense fallback={<p className="text-sm text-muted">{t("provider.loadingConfig")}</p>}><DeploymentPanel /></Suspense>}
      </Modal>
    </div>}
  </AsyncState>;
}
