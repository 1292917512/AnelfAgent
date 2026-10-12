import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { apiErrorMessage, retrievalApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { AsyncState } from "@/components/common/AsyncState";
import { Button, Input, Switch } from "@/components/ui";

export function RetrievalSettingsPanel() {
  const { t } = useTranslation("retrieval");
  const client = useQueryClient();
  const [proxy, setProxy] = useState<string | null>(null);
  const query = useQuery({ queryKey: ["retrievalSettings"], queryFn: () => retrievalApi.settings().then((r) => r.data), throwOnError: false });
  const save = useMutation({
    mutationFn: (data: { proxy?: string; ssrf_protection?: boolean }) => retrievalApi.saveSettings(data),
    onSuccess: async (_, variables) => {
      await client.invalidateQueries({ queryKey: ["retrievalSettings"] });
      if (variables.proxy !== undefined) setProxy(null);
    },
  });
  const data = query.data;
  const value = proxy ?? data?.proxy ?? "";
  return <AsyncState pending={query.isPending} error={query.error} retry={() => void query.refetch()}>
    {data && <div className="grid items-start gap-4 xl:grid-cols-2">
      <Card title={t("settings.proxy")} subtitle={t("settings.proxySubtitle")}>
        <form className="flex flex-col gap-3 sm:flex-row" onSubmit={(event) => { event.preventDefault(); if (!save.isPending) save.mutate({ proxy: value.trim() }); }}>
          <Input value={value} onChange={(event) => setProxy(event.target.value)} disabled={save.isPending}
            aria-label={t("settings.proxy")} placeholder={t("settings.proxyPlaceholder")} className="min-w-0 flex-1 font-mono text-xs" />
          <Button type="submit" size="sm" loading={save.isPending} disabled={value.trim() === data.proxy}>{t("common:save")}</Button>
        </form>
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
