/**
 * 工具调用块共享组件 — StreamingArea（流式过程）与 MessageRow（固化卡片）复用。
 */
import { useState } from "react";
import { Loader2, Check, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ChatStreamingTool } from "@/lib/types";

export const READONLY_TOOLS = new Set([
  "read_file", "search_files", "list_directory", "file_info",
  "web_fetch", "web_search", "extract_page_links", "recall",
]);

export function ToolStatusIcon({ status }: { status: string }) {
  if (status === "running") return <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />;
  if (status === "done") return <Check className="h-3.5 w-3.5 text-green-500" />;
  return <X className="h-3.5 w-3.5 text-red-500" />;
}

/** 一行式工具调用标题（对齐 Claude Code userFacingName 风格） */
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
  const hasResult = Boolean(tool.result_preview);
  return (
    <div className="rounded border border-border/60 bg-muted/40 px-2.5 py-1.5 text-xs">
      <button
        onClick={() => hasResult && setOpen(!open)}
        className={cn("flex items-center gap-2 w-full text-left", hasResult && "cursor-pointer")}
      >
        <ToolStatusIcon status={tool.status} />
        <span className="font-mono text-foreground/80 truncate">{toolTitle(tool.name, tool.arguments)}</span>
        {tool.duration_ms != null && tool.status !== "running" && (
          <span className="text-muted shrink-0">{(tool.duration_ms / 1000).toFixed(1)}s</span>
        )}
      </button>
      {open && tool.result_preview && (
        <pre className="mt-1.5 max-h-32 overflow-auto whitespace-pre-wrap break-all text-muted border-t border-border/40 pt-1.5">
          {tool.result_preview}
        </pre>
      )}
    </div>
  );
}
