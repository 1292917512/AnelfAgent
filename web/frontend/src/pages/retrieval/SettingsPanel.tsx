import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { apiErrorMessage, retrievalApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { AsyncState } from "@/components/common/AsyncState";
import { NetworkConfigLink } from "@/components/common/NetworkSettings";
import { Switch } from "@/components/ui";

export function RetrievalSettingsPanel() {
  const { t } = useTranslation("retrieval");
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["retrievalSettings"], queryFn: () => retrievalApi.settings().then((r) => r.data), throwOnError: false });
  const save = useMutation({
    mutationFn: (data: { ssrf_protection?: boolean }) => retrievalApi.saveSettings(data),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ["retrievalSettings"] });
    },
  });
  const data = query.data;
  return <AsyncState pending={query.isPending} error={query.error} retry={() => void query.refetch()}>
    {data && <div className="grid items-start gap-4 xl:grid-cols-2">
      <Card title={t("settings.proxy")} subtitle={t("settings.proxySubtitle")}>
        <NetworkConfigLink />
      </Card>
      <Card title={t("settings.ssrf")} subtitle={t("settings.ssrfSubtitle")}>
        <div className="flex items-center gap-3 text-sm">
          <Switch label={t("settings.ssrf")} checked={data.ssrf_protection} disabled={save.isPending} onChange={(enabled) => save.mutate({ ssrf_protection: enabled })} />
          <span>{t(data.ssrf_protection ? "settings.ssrfOn" : "settings.ssrfOff")}</span>
        </div>
      </Card>
      {save.isError && <p role="alert" className="text-sm text-danger xl:col-span-2">{apiErrorMessage(save.error, t("common:requestFailed"))}</p>}
    </div>}
  </AsyncState>;
}
