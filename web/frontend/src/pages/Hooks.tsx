import { useRouteTab } from "@/hooks/useRouteTab";
import { useTranslation } from "react-i18next";
import { Webhook, Zap } from "lucide-react";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { PageContainer, PageIntro } from "@/components/common/PageContainer";
import { HooksPanel } from "./settings/HooksPanel";
import { LlmHooksPanel } from "./settings/LlmHooksPanel";

type HooksTab = "user" | "llm";

/** 钩子页（侧边栏独立入口）：用户脚本钩子 + LLM 钩子面 观测与配置。 */
export default function Hooks() {
  const { t } = useTranslation("settings");
  const [tab, setTab] = useRouteTab<HooksTab>(["user", "llm"], "user");

  const TABS: TabItem<HooksTab>[] = [
    { key: "user", label: t("hooks.title"), icon: Webhook },
    { key: "llm", label: t("llmHooks.title"), icon: Zap },
  ];

  return (
    <PageContainer>
      <PageIntro />
      <TabBar tabs={TABS} activeTab={tab} onChange={setTab} />
      {tab === "user" && <HooksPanel />}
      {tab === "llm" && <LlmHooksPanel />}
    </PageContainer>
  );
}
