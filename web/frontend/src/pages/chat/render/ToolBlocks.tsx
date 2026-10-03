/**
 * 工具调用块共享组件 — StreamingArea（流式过程）与 MessageRow（固化卡片）复用。
 */
import { useState } from "react";
import { Loader2, Check, X } from "lucide-react";
import { formatElapsedCompact } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { ChatStreamingTool } from "@/lib/types";
import { useWorkbenchStore } from "@/stores/workbench-store";

export const READONLY_TOOLS = new Set([
  "read_file", "search_files", "list_directory", "file_info",
  "web_fetch", "web_search", "extract_page_links", "recall",
]);

function ToolStatusIcon({ status }: { status: string }) {
  if (status === "running") return <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />;
  if (status === "done") return <Check className="h-3.5 w-3.5 text-green-500" />;
  return <X className="h-3.5 w-3.5 text-red-500" />;
}

/** 从工具参数提取工作区相对路径（可点击打开的前提；绝对路径返回 null——
 * 前端没有工作区绝对根，无法可靠归一，避免与树内相对路径形成双标签） */
export function toolRelativePath(args?: string): string | null {
  if (!args) return null;
  try {
    const parsed = JSON.parse(args);
    const p = parsed.path ?? parsed.file_path;
    if (typeof p === "string" && p && !p.startsWith("/") && !p.startsWith("~")) return p;
  } catch { /* arguments_preview 可能不是完整 JSON */ }
  return null;
}

/** 一行式工具调用标题（用户可读的动词化命名） */
export function toolTitle(name: string, args?: string): string {
  if (!args) return name;
  try {
    const parsed = JSON.parse(args);
    const key = parsed.path ?? parsed.file_path ?? parsed.command ?? parsed.query ?? parsed.url;
    if (typeof key === "string" && key) {
      const short = key.length > 48 ? key.slice(0, 48) + "…" : key;
      return `${name}(${short})`;
    }
  } catch { /* arguments_preview 可能不是完整 JSON */ }
  return name;
}

export function ToolBlock({ tool }: { tool: ChatStreamingTool }) {
  const [open, setOpen] = useState(false);
  const openFile = useWorkbenchStore((s) => s.openFile);
  const hasResult = Boolean(tool.result_preview);
  // ZCode 式文件 chip：工具参数带工作区相对路径时标题可点击打开（跳编辑器）
  const relPath = toolRelativePath(tool.arguments);
  return (
    <div className="rounded border border-border/60 bg-muted/40 px-2.5 py-1.5 text-xs">
      <div className="flex items-center gap-2 w-full">
        <button
          onClick={() => hasResult && setOpen(!open)}
          className={cn("flex items-center gap-2 min-w-0 flex-1 text-left", hasResult && "cursor-pointer")}
        >
          <ToolStatusIcon status={tool.status} />
          {relPath ? (
            <span
              role="link"
              tabIndex={0}
              title={relPath}
              className="font-mono text-accent truncate hover:underline cursor-pointer"
              onClick={(e) => {
                e.stopPropagation();
                openFile(relPath);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.stopPropagation();
                  openFile(relPath);
                }
              }}
            >
              {toolTitle(tool.name, tool.arguments)}
            </span>
          ) : (
            <span className="font-mono text-foreground/80 truncate">{toolTitle(tool.name, tool.arguments)}</span>
          )}
        </button>
        {tool.duration_ms != null && tool.status !== "running" && (
          <span className="text-muted shrink-0 font-mono tabular-nums">{formatElapsedCompact(tool.duration_ms)}</span>
        )}
      </div>
      {open && tool.result_preview && (
        <pre className="mt-1.5 max-h-32 overflow-auto whitespace-pre-wrap break-all text-muted border-t border-border/40 pt-1.5">
          {tool.result_preview}
        </pre>
      )}
    </div>
  );
}
