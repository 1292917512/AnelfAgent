import { useState } from "react";
import { useTranslation } from "react-i18next";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { PageContainer, PageHeader } from "@/components/common/PageContainer";
import { Database, Smile } from "lucide-react";
import { DatabasePanel } from "@/pages/database/DatabasePanel";
import { StickersPanel } from "@/pages/stickers/StickersPanel";

type DataTab = "database" | "stickers";

/** 数据管理 — 所有数据类功能的统一入口（数据库 / 表情包，后续可扩展新 Tab） */
export default function Data() {
  const { t } = useTranslation("data");
  const [tab, setTab] = useState<DataTab>("database");

  const TABS: TabItem<DataTab>[] = [
    { key: "database", label: t("tabs.database"), icon: Database },
    { key: "stickers", label: t("tabs.stickers"), icon: Smile },
  ];

  return (
    <PageContainer wide>
      <PageHeader
        icon={<Database size={20} className="text-accent" />}
        title={t("title")}
        subtitle={t("subtitle")}
      />
      <TabBar tabs={TABS} activeTab={tab} onChange={setTab} />
      {tab === "database" && <DatabasePanel />}
      {tab === "stickers" && <StickersPanel />}
    </PageContainer>
  );
}
