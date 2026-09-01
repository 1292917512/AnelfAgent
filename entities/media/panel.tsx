import { registerPluginI18n } from "@/lib/plugin-i18n";
import zh from "./media/locales/zh.json";
import en from "./media/locales/en.json";

registerPluginI18n("media", { zh, en });

/**
 * 媒体库实体自定义面板 — provider 优先级、音色与默认参数、风格预设。
 *
 * 通过 scripts/link_entity_panels.py 软链接到前端 panels 目录，
 * Vite import.meta.glob 自动发现并懒加载。
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowUpDown, Palette, SlidersHorizontal } from "lucide-react";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { PriorityPanel } from "./media/PriorityPanel";
import { DefaultsPanel } from "./media/DefaultsPanel";
import { StylesPanel } from "./media/StylesPanel";

type MediaTab = "priority" | "defaults" | "styles";

export default function MediaPanel() {
  const { t } = useTranslation("media");
  const [tab, setTab] = useState<MediaTab>("priority");

  const TABS: TabItem<MediaTab>[] = [
    { key: "priority", label: t("tabs.priority"), icon: ArrowUpDown },
    { key: "defaults", label: t("tabs.defaults"), icon: SlidersHorizontal },
    { key: "styles", label: t("tabs.styles"), icon: Palette },
  ];

  return (
    <div className="space-y-4">
      <TabBar tabs={TABS} activeTab={tab} onChange={setTab} />
      {tab === "priority" && <PriorityPanel />}
      {tab === "defaults" && <DefaultsPanel />}
      {tab === "styles" && <StylesPanel />}
    </div>
  );
}
