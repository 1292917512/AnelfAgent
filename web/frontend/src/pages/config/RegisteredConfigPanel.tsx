import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { configMetaApi } from "@/lib/api";
import { AsyncState } from "@/components/common/AsyncState";
import { ConfigSection } from "./ConfigSection";
import { ConfigDetailDrawer } from "./ConfigDetailDrawer";

/** 按注册分组展示配置，读写与配置中心共用元数据和保存入口。 */
export function RegisteredConfigPanel({ group }: { group: string }) {
  const { t } = useTranslation("entities");
  const [detailKey, setDetailKey] = useState<string | null>(null);
  const query = useQuery({ queryKey: ["configMeta"], queryFn: () => configMetaApi.list().then((response) => response.data), throwOnError: false });
  const items = query.data?.groups.find((entry) => entry.group === group)?.items ?? [];
  return <AsyncState pending={query.isPending} error={query.error} retry={() => void query.refetch()}>
    <p className="mb-4 text-sm leading-relaxed text-muted">{t("config.autoSave")}</p>
    {items.length ? <ConfigSection items={items} onOpenDetail={(item) => setDetailKey(item.key)} /> : <p className="py-8 text-center text-sm text-muted">{t("config.empty")}</p>}
    <ConfigDetailDrawer group={group} item={items.find((item) => item.key === detailKey) ?? null} onClose={() => setDetailKey(null)} />
  </AsyncState>;
}
