/** mention 感知的 Markdown 渲染：把 `[name](./path)` 链接段拆出渲染为可点击文件 chip，其余段落走原 Markdown 管线。 */

import { memo, type ReactNode } from "react";
import { FileText } from "lucide-react";
import { useWorkbenchStore } from "@/stores/workbench-store";
import { CollapsibleMarkdown } from "./CollapsibleMarkdown";
import { splitMentions, type FileMention } from "../mention/mentionMarkdown";

/** 单个文件引用 chip（点击打开编辑器并聚焦） */
function MentionChip({ mention, index }: { mention: FileMention; index: number }) {
  const openFile = useWorkbenchStore((s) => s.openFile);
  const setFileTreeFocus = useWorkbenchStore((s) => s.setFileTreeFocus);
  return (
    <button
      key={index}
      type="button"
      title={mention.path}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        openFile(mention.path);
        setFileTreeFocus(mention.path);
      }}
      className="mx-0.5 inline-flex items-center gap-1 rounded border border-border bg-elevated px-1.5 py-0 align-baseline font-mono text-[12px] text-accent hover:bg-accent/10 transition-colors"
    >
      <FileText size={11} />
      <span className="max-w-[180px] truncate">{mention.name}</span>
    </button>
  );
}

/** mention 感知的消息正文：mention 段渲染为文件 chip，普通段走 CollapsibleMarkdown */
export const MentionMarkdown = memo(function MentionMarkdown({
  content,
  fadeClass,
}: {
  content: string;
  fadeClass: string;
}) {
  const parts = splitMentions(content);
  const hasMention = parts.some((p) => typeof p !== "string");
  if (!hasMention) {
    return <CollapsibleMarkdown content={content} fadeClass={fadeClass} />;
  }
  const nodes: ReactNode[] = [];
  parts.forEach((part, i) => {
    if (typeof part === "string") {
      if (part) nodes.push(<CollapsibleMarkdown key={i} content={part} fadeClass={fadeClass} />);
    } else {
      nodes.push(<MentionChip key={i} mention={part} index={i} />);
    }
  });
  return <>{nodes}</>;
});
