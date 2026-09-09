import { registerPluginI18n } from "@/lib/plugin-i18n";
import zh from "./vault/locales/zh.json";
import en from "./vault/locales/en.json";

registerPluginI18n("vault", { zh, en });

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ArrowLeftRight, KeyRound, Lock, ShieldCheck, Timer, Wand2 } from "lucide-react";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { vaultApi } from "./vault/api";
import type { VaultEntry } from "./vault/types";
import { UnlockScreen } from "./vault/UnlockScreen";
import { VaultList } from "./vault/VaultList";
import { EntryForm } from "./vault/EntryForm";
import { GeneratorPanel } from "./vault/GeneratorPanel";
import { SecurityPanel } from "./vault/SecurityPanel";
import { PortablePanel } from "./vault/PortablePanel";

type VaultTab = "entries" | "generator" | "security" | "portable";

export default function VaultPanel() {
  const { t } = useTranslation("vault");
  const [tab, setTab] = useState<VaultTab>("entries");
  // null = 列表视图；"new" = 新增表单；VaultEntry = 编辑表单
  const [formState, setFormState] = useState<"new" | VaultEntry | null>(null);

  const { data: status, refetch } = useQuery({
    queryKey: ["vaultStatus"],
    queryFn: () => vaultApi.status().then((r) => r.data),
    refetchInterval: 30_000, // 同步自动锁定状态
  });

  if (!status) {
    return <div className="py-16 text-center text-sm text-muted">{t("loading")}</div>;
  }

  if (!status.initialized) {
    return <UnlockScreen mode="setup" onDone={() => refetch()} />;
  }
  if (!status.unlocked) {
    return (
      <UnlockScreen
        mode="unlock"
        machine={status.unlock_mode === "machine"}
        onDone={() => refetch()}
      />
    );
  }

  const TABS: TabItem<VaultTab>[] = [
    { key: "entries", label: t("tabs.entries"), icon: KeyRound },
    { key: "generator", label: t("tabs.generator"), icon: Wand2 },
    { key: "security", label: t("tabs.security"), icon: ShieldCheck },
    { key: "portable", label: t("tabs.portable"), icon: ArrowLeftRight },
  ];

  const lockNow = async () => {
    await vaultApi.lock();
    refetch();
  };

  const isMaster = status.unlock_mode === "master";

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <TabBar tabs={TABS} activeTab={tab} onChange={(next) => { setTab(next); setFormState(null); }} />
        <div className="flex items-center gap-2 text-xs text-muted">
          {isMaster && status.auto_lock_remaining > 0 && (
            <span className="flex items-center gap-1">
              <Timer size={12} />
              {t("autoLock", { minutes: Math.ceil(status.auto_lock_remaining / 60) })}
            </span>
          )}
          {!isMaster && (
            <span className="flex items-center gap-1 text-ok">
              <Timer size={12} /> {t("machineMode")}
            </span>
          )}
          {isMaster && (
            <button
              onClick={lockNow}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-elevated text-muted hover:bg-hover transition-all"
            >
              <Lock size={13} /> {t("actions.lockNow")}
            </button>
          )}
        </div>
      </div>

      {tab === "entries" && (
        formState === null ? (
          <VaultList
            onCreate={() => setFormState("new")}
            onEdit={(entry) => setFormState(entry)}
          />
        ) : (
          <EntryForm
            initial={formState === "new" ? null : formState}
            onDone={() => setFormState(null)}
          />
        )
      )}
      {tab === "generator" && <GeneratorPanel />}
      {tab === "security" && (
        <SecurityPanel
          unlockMode={isMaster ? "master" : "machine"}
          onModeChanged={() => refetch()}
          onLocked={() => refetch()}
        />
      )}
      {tab === "portable" && <PortablePanel />}
    </div>
  );
}
