import { useRouteTab } from "@/hooks/useRouteTab";
import { useTranslation } from "react-i18next";
import { Brain, Workflow, Webhook, Zap } from "lucide-react";
import { HookOverview } from "./hooks/HookOverview";
import { RecallPolicyPanel } from "./memory/RecallPolicyPanel";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { PageContainer, PageIntro } from "@/components/common/PageContainer";
import { HooksPanel } from "./settings/HooksPanel";
import { LlmHooksPanel } from "./settings/LlmHooksPanel";

type HooksTab = "overview" | "user" | "llm" | "recall";

/** 钩子页（侧边栏独立入口）：用户脚本钩子 + LLM 钩子面 观测与配置。 */
export default function Hooks() {
  const { t } = useTranslation("settings");
  const [tab, setTab] = useRouteTab<HooksTab>(["overview", "user", "llm", "recall"], "overview");

  const TABS: TabItem<HooksTab>[] = [
    { key: "overview", label: t("hookFlow.title"), icon: Workflow },
    { key: "recall", label: t("recallPolicy.title"), icon: Brain },
    { key: "user", label: t("hooks.title"), icon: Webhook },
    { key: "llm", label: t("llmHooks.title"), icon: Zap },
  ];

  return (
    <PageContainer>
      <PageIntro />
      <TabBar tabs={TABS} activeTab={tab} onChange={setTab} />
      {tab === "overview" && <HookOverview />}
      {tab === "recall" && <RecallPolicyPanel />}
      {tab === "user" && <HooksPanel />}
      {tab === "llm" && <LlmHooksPanel />}
    </PageContainer>
  );
}
