import { useTranslation } from "react-i18next";
import { useChatStore } from "@/stores/chat-store";
import { usePlanStore } from "@/stores/plan-store";
import { useDelegationStore } from "@/stores/delegation-store";
import { DelegationCard } from "@/components/delegation/DelegationCard";
import { PlanCard } from "./render/PlanCard";
import { ThinkingBlock } from "./render/ThinkingBlock";
import { ToolCallsCard } from "./render/ToolCallsCard";
import { ToolSummaryCard } from "./render/ToolSummaryCard";
import { ChangesCard } from "./render/ChangesCard";
import { DiffView } from "./DiffView";

/** Web 保存的操作记录独立于交流消息，追踪关闭时仍可查看。 */
export default function WebActivityPanel() {
  const { t } = useTranslation("workbench");
  const chatId = useChatStore((state) => state.activeChatId);
  const chats = useChatStore((state) => state.chats);
  const switchChat = useChatStore((state) => state.setActiveChat);
  const bucket = useChatStore((state) => state.buckets[chatId]);
  const plans = usePlanStore((state) => state.plans[chatId]);
  const agents = useDelegationStore((state) => state.delegations[chatId]);
  const records = bucket?.messages.filter((message) => message.toolCalls?.length || message.thinking || message.changes?.length || message.summary) ?? [];
  const stream = bucket?.streaming;
  return <div className="flex h-full min-h-0 flex-col">
    <div className="shrink-0 border-b border-border px-4 py-3"><label className="block text-xs text-muted">{t("webChat")}
      <select className="mt-2 h-10 w-full rounded-lg border border-input bg-card px-3 text-sm text-heading" value={chatId} onChange={(event) => void switchChat(event.target.value)}>
        {chats.map((chat) => <option key={chat.chat_id} value={chat.chat_id}>{chat.title || chat.chat_id}</option>)}
      </select></label><p className="mt-2 text-xs leading-relaxed text-muted">{t("execution.webRecordsHint")}</p></div>
    <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
      {Object.values(plans ?? {}).map((plan) => <PlanCard key={plan.plan_id} plan={plan} />)}
      {Object.values(agents ?? {}).map((node) => <DelegationCard key={node.delegation_id} node={node} compact />)}
      {records.map((message, index) => <section key={message.cid ?? message.id ?? index} className="execution-record space-y-3">
        {message.ts && <p className="text-xs text-muted">{new Date(message.ts * 1000).toLocaleString()}</p>}
        {message.thinking && <ThinkingBlock reasoning={message.thinking} active={false} />}
        {!!message.toolCalls?.length && <ToolCallsCard tools={message.toolCalls} />}
        {message.summary && <ToolSummaryCard summary={message.summary} />}
        {!!message.changes?.length && <ChangesCard changes={message.changes} />}
      </section>)}
      {stream && <section className="execution-record space-y-3">
        {stream.reasoning && <ThinkingBlock reasoning={stream.reasoning} active={!stream.text} />}
        {!!stream.tools.length && <ToolCallsCard tools={stream.tools} />}
        {stream.diffs.map((diff, index) => <DiffView key={`${diff.path}:${index}`} {...diff} />)}
      </section>}
      {!records.length && !stream && !Object.keys(plans ?? {}).length && !Object.keys(agents ?? {}).length && <p className="py-12 text-center text-sm text-muted">{t("execution.noRecords")}</p>}
    </div>
  </div>;
}
