import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ConfigLink } from "@/components/common/ConfigLink";
import { TabBar } from "@/components/common/TabBar";
import { AsyncState } from "@/components/common/AsyncState";
import { ConfigFormPanel } from "@/pages/config/ConfigFormPanel";
import { memoryApi } from "@/lib/api";
import type { CogneeConfig, CogneeSettingsSection, CogneeStatus, ConfigValues } from "@/lib/types";
import { ModelConfigCard } from "./ModelConfigCard";

const sections: CogneeSettingsSection[] = ["models", "recall", "sync", "storage"];

export function CogneeSettings({ status }: { status?: CogneeStatus }) {
  const { t } = useTranslation("memory");
  const [section, setSection] = useState<CogneeSettingsSection>("models");
  const query = useQuery({ queryKey: ["cogneeConfig"],
    queryFn: () => memoryApi.cognee.getConfig().then((response) => response.data), throwOnError: false });
  const config = query.data;
  return <div className="space-y-4">
    <TabBar tabs={sections.map((key) => ({ key, label: t(`cognee.settings.${key}`) }))}
      activeTab={section} onChange={setSection} fill />
    <p className="text-sm leading-relaxed text-muted">{t(`cognee.settings.${section}Hint`)}</p>
    {status?.sync.configuration_pending && <p role="status" className="rounded-lg bg-accent/5 p-3 text-sm text-accent">{t("cognee.applyPending")}</p>}
    {status?.sync.configuration_error && <p role="alert" className="text-sm text-danger">{t("cognee.applyFailed")}: {status.sync.configuration_error}</p>}
    <AsyncState pending={query.isPending} error={!config ? query.error : undefined} retry={() => void query.refetch()}>
      {config && sections.map((key) => <div key={key} hidden={section !== key} className="space-y-4"
        role="tabpanel" aria-label={t(`cognee.settings.${key}`)}>
        {key === "models" && <>
          <div className="grid items-stretch gap-4 lg:grid-cols-2">
            <ModelConfigCard kind="chat" resolved={status?.resolved.chat} />
            <ModelConfigCard kind="embedding" resolved={status?.resolved.embedding} />
          </div>
          <Link to="/models" className="inline-block text-xs text-muted hover:text-accent hover:underline">{t("cognee.configureModels")}</Link>
        </>}
        {key === "recall" && <ConfigLink config={{ key: "memory_probe_timeout_seconds" }} className="text-sm">
          {t("cognee.settings.recallBudgetLink")}
        </ConfigLink>}
        {key === "storage" && <Link to="/data" className="inline-block text-sm text-accent hover:underline">
          {t("cognee.settings.storageLink")}
        </Link>}
        {key !== "models" && <ConfigFormPanel title={t(`cognee.settings.${key}`)}
          fields={config.options.settings_fields.filter((field) => field.section === key).map((field) => ({
            ...field, label: t(`cogneeFields.${field.key}`), desc: t(`cogneeDescs.${field.key}`),
          }))}
          queryKey="cogneeConfig"
          fetchFn={() => memoryApi.cognee.getConfig().then((response) => ({ ...response.data }))}
          saveFn={(patch: ConfigValues) => memoryApi.cognee.saveConfig(patch as Partial<CogneeConfig>)}
          extraInvalidateKeys={["cogneeStatus", "memoryHealth"]}
          confirmBooleanChanges
          note={t("cognee.hotApplyNote")} advancedLabel={t("cognee.settings.advanced")} />}
      </div>)}
    </AsyncState>
  </div>;
}
