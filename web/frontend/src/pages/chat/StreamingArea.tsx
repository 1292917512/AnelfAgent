import { useChatStore } from "@/stores/chat-store";
import { Markdown } from "./render/Markdown";

/** Web 回复增量正文；思考与工具结果由独立执行视图呈现。 */
export function StreamingArea() {
  const text = useChatStore((state) => state.buckets[state.activeChatId]?.streaming?.text ?? "");
  if (!text) return null;
  return <div className="text-sm leading-relaxed"><Markdown content={text} /><span className="inline-block h-4 w-1 rounded bg-accent animate-pulse-subtle" /></div>;
}
