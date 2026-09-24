/** mention 感知的 Markdown 渲染：把 `[name](./path)` 链接段拆出渲染为可点击文件 chip，其余段落走原 Markdown 管线。 */

import { memo, type ReactNode } from "react";
import { FileText, Folder } from "lucide-react";
import { useWorkbenchStore } from "@/stores/workbench-store";
import { CollapsibleMarkdown } from "./CollapsibleMarkdown";
import { splitMentions, type FileMention } from "../mention/mentionMarkdown";

/** 解析 mention 路径前缀（project: 所属根 / dir: 目录标记），返回真实路径与归属 */
function resolveMentionPath(raw: string): { path: string; root: "workspace" | "project"; isDir: boolean } {
  let rest = raw;
  const root = rest.startsWith("project:") ? "project" : "workspace";
  if (root === "project") rest = rest.slice("project:".length);
  const isDir = rest.startsWith("dir:");
  if (isDir) rest = rest.slice("dir:".length);
  return { path: rest, root, isDir };
}

/** 单个引用 chip：文件点打开编辑器，目录聚焦左侧文件树对应目录 */
function MentionChip({ mention, index }: { mention: FileMention; index: number }) {
  const openFile = useWorkbenchStore((s) => s.openFile);
  const setFileTreeFocus = useWorkbenchStore((s) => s.setFileTreeFocus);
  const toggleLeft = useWorkbenchStore((s) => s.toggleLeft);
  const leftOpen = useWorkbenchStore((s) => s.leftOpen);
  const { path, root, isDir } = resolveMentionPath(mention.path);
  return (
    <button
      key={index}
      type="button"
      title={mention.path}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        if (isDir) {
          // 目录：打开左栏并聚焦对应目录（不开编辑器）
          if (!leftOpen) toggleLeft();
          setFileTreeFocus(path);
        } else {
          openFile(path, root);
          setFileTreeFocus(path);
        }
      }}
      className="mx-0.5 inline-flex items-center gap-1 rounded border border-border bg-elevated px-1.5 py-0 align-baseline font-mono text-[12px] text-accent hover:bg-accent/10 transition-colors"
    >
      {isDir ? <Folder size={11} /> : <FileText size={11} />}
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
