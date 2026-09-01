import { registerPluginI18n } from "@/lib/plugin-i18n";
import zh from "./ssh/locales/zh.json";
import en from "./ssh/locales/en.json";

registerPluginI18n("ssh", { zh, en });

import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Network, SquareTerminal } from "lucide-react";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import type { SshConnection } from "./ssh/types";
import { ConnectionsPanel } from "./ssh/ConnectionsPanel";
import { ConnectionForm } from "./ssh/ConnectionForm";
import { ExecPanel } from "./ssh/ExecPanel";

type SshTab = "connections" | "exec";

export default function SshPanel() {
  const { t } = useTranslation("ssh");
  const [tab, setTab] = useState<SshTab>("connections");
  // null = 列表视图；"new" = 新增表单；SshConnection = 编辑表单
  const [formState, setFormState] = useState<"new" | SshConnection | null>(null);

  const TABS: TabItem<SshTab>[] = [
    { key: "connections", label: t("tabs.connections"), icon: Network },
    { key: "exec", label: t("tabs.exec"), icon: SquareTerminal },
  ];

  return (
    <div className="space-y-4">
      <TabBar tabs={TABS} activeTab={tab} onChange={(next) => { setTab(next); setFormState(null); }} />
      {tab === "connections" && (
        formState === null ? (
          <ConnectionsPanel
            onCreate={() => setFormState("new")}
            onEdit={(conn) => setFormState(conn)}
          />
        ) : (
          <ConnectionForm
            initial={formState === "new" ? null : formState}
            onDone={() => setFormState(null)}
          />
        )
      )}
      {tab === "exec" && <ExecPanel />}
    </div>
  );
}
