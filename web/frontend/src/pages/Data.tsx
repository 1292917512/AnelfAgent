import { useRouteTab } from "@/hooks/useRouteTab";
import { useTranslation } from "react-i18next";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { PageContainer, PageHeader } from "@/components/common/PageContainer";
import { Database, HardDrive, Layers } from "lucide-react";
import { DatabasePanel } from "@/pages/database/DatabasePanel";
import { getUiContributions } from "@/lib/ui-contribution-registry";
import { ContributionContent } from "@/components/extensions/ContributionSlot";
import { StoragePanel } from "@/pages/database/StoragePanel";
import { VolumePanel } from "@/pages/database/volumes/VolumePanel";

/** 数据管理与模块自注册的数据面板。 */
export default function Data() {
  const { t } = useTranslation("data");
  const extensions = getUiContributions("data.tabs");
  const [tab, setTab] = useRouteTab(["database", "volumes", "storage", ...extensions.map((entry) => entry.tab)], "database");
  const extension = extensions.find((entry) => entry.tab === tab);

  const TABS: TabItem<string>[] = [
    { key: "database", label: t("tabs.database"), icon: Database },
    { key: "volumes", label: t("tabs.volumes"), icon: Layers },
    ...extensions.map((entry) => ({ key: entry.tab, label: t(entry.title.key, { ns: entry.title.ns }), icon: entry.icon })),
    { key: "storage", label: t("tabs.storage"), icon: HardDrive },
  ];

  return (
    <PageContainer>
      <PageHeader
        icon={<Database size={20} className="text-accent" />}
        title={t("title")}
        subtitle={t("subtitle")}
      />
      <TabBar tabs={TABS} activeTab={tab} onChange={setTab} />
      {tab === "database" && <DatabasePanel />}
      {tab === "volumes" && <VolumePanel />}
      {extension && <ContributionContent key={extension.key} entry={extension} componentProps={{}} />}
      {tab === "storage" && <StoragePanel />}
    </PageContainer>
  );
}
