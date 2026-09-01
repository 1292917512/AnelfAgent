import { registerPluginI18n } from "@/lib/plugin-i18n";
import zh from "./web/locales/zh.json";
import en from "./web/locales/en.json";

registerPluginI18n("web", { zh, en });

/**
 * web 实体自定义面板 — 能力 × 提供者矩阵 + 通用设置。
 *
 * 通过 scripts/link_entity_panels.py 软链接到前端 panels 目录，
 * Vite import.meta.glob 自动发现并懒加载。
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { LayoutGrid, Settings2 } from "lucide-react";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { ProvidersPanel } from "./web/ProvidersPanel";
import { SettingsPanel } from "./web/SettingsPanel";

type WebTab = "providers" | "settings";

export default function WebPanel() {
  const { t } = useTranslation("web");
  const [tab, setTab] = useState<WebTab>("providers");

  const TABS: TabItem<WebTab>[] = [
    { key: "providers", label: t("tabs.providers"), icon: LayoutGrid },
    { key: "settings", label: t("tabs.settings"), icon: Settings2 },
  ];

  return (
    <div className="space-y-4">
      <TabBar tabs={TABS} activeTab={tab} onChange={setTab} />
      {tab === "providers" ? <ProvidersPanel /> : <SettingsPanel />}
    </div>
  );
}
