import { useState } from "react";
import { useTranslation } from "react-i18next";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { PageContainer } from "@/components/common/PageContainer";
import { SysConfigPanel } from "./settings/SysConfigPanel";
import { SystemPanel } from "./settings/SystemPanel";
import { PythonPanel } from "./settings/PythonPanel";
import { GitPanel } from "./settings/GitPanel";
import { ConfigStatusPanel } from "./settings/ConfigStatusPanel";

type SettingsTab = "sysConfig" | "system" | "python" | "git" | "config";

export default function Settings() {
  const { t } = useTranslation("settings");
  const [tab, setTab] = useState<SettingsTab>("sysConfig");

  const TAB_KEYS: TabItem<SettingsTab>[] = [
    { key: "sysConfig", label: t("tabs.sysConfig") },
    { key: "system", label: t("tabs.system") },
    { key: "python", label: t("tabs.python") },
    { key: "git", label: t("tabs.git") },
    { key: "config", label: t("tabs.config") },
  ];

  return (
    <PageContainer wide>
      <TabBar tabs={TAB_KEYS} activeTab={tab} onChange={setTab} />

      {tab === "sysConfig" && <SysConfigPanel />}
      {tab === "system" && <SystemPanel />}
      {tab === "python" && <PythonPanel />}
      {tab === "git" && <GitPanel />}
      {tab === "config" && <ConfigStatusPanel />}
    </PageContainer>
  );
}
