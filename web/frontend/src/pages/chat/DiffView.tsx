/**
 * DiffView — unified diff 渲染（红绿增删 + 行号，对齐 Claude Code StructuredDiff）。
 *
 * 过程性展示：edit_file 的 diff 经 file_diff 事件到达，
 * 只在流式过程区显示，不落对话历史。
 */
import { useState } from "react";
import { FileDiff, ChevronDown, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

interface DiffLine {
  kind: "add" | "del" | "context" | "meta";
  text: string;
  oldNo?: number;
  newNo?: number;
}

function parseUnifiedDiff(diff: string): DiffLine[] {
  const lines: DiffLine[] = [];
  let oldNo = 0;
  let newNo = 0;
  for (const raw of diff.split("\n")) {
    if (raw.startsWith("---") || raw.startsWith("+++")) continue;
    const hunk = raw.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
    if (hunk) {
      oldNo = parseInt(hunk[1] ?? "0", 10);
      newNo = parseInt(hunk[2] ?? "0", 10);
      lines.push({ kind: "meta", text: raw });
      continue;
    }
    if (raw.startsWith("+")) {
      lines.push({ kind: "add", text: raw.slice(1), newNo: newNo++ });
    } else if (raw.startsWith("-")) {
      lines.push({ kind: "del", text: raw.slice(1), oldNo: oldNo++ });
    } else {
      lines.push({ kind: "context", text: raw.slice(1), oldNo: oldNo++, newNo: newNo++ });
    }
  }
  return lines;
}

export function DiffView({
  path,
  diff,
  additions,
  removals,
}: {
  path: string;
  diff: string;
  additions: number;
  removals: number;
}) {
  const [open, setOpen] = useState(true);
  const lines = parseUnifiedDiff(diff);
  const fileName = path.split("/").pop() ?? path;

  return (
    <div className="rounded border border-border/60 bg-muted/40 text-xs overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 w-full px-2.5 py-1.5 text-left hover:bg-muted/60 transition-colors"
      >
        {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        <FileDiff className="h-3.5 w-3.5 text-primary" />
        <span className="font-mono text-foreground/80 truncate">{fileName}</span>
        <span className="shrink-0 text-green-600">+{additions}</span>
        <span className="shrink-0 text-red-500">-{removals}</span>
      </button>
      {open && (
        <div className="border-t border-border/40 overflow-x-auto max-h-64 overflow-y-auto">
          <table className="w-full font-mono">
            <tbody>
              {lines.map((line, i) => (
                <tr
                  key={i}
                  className={cn(
                    line.kind === "add" && "bg-green-500/10",
                    line.kind === "del" && "bg-red-500/10",
                    line.kind === "meta" && "bg-primary/5 text-muted",
                  )}
                >
                  <td className="w-10 select-none px-1.5 text-right text-muted/60 align-top">
                    {line.oldNo ?? ""}
                  </td>
                  <td className="w-10 select-none px-1.5 text-right text-muted/60 align-top">
                    {line.newNo ?? ""}
                  </td>
                  <td
                    className={cn(
                      "px-2 whitespace-pre-wrap break-all",
                      line.kind === "add" && "text-green-700 dark:text-green-300",
                      line.kind === "del" && "text-red-600 dark:text-red-300",
                    )}
                  >
                    {line.kind === "add" ? "+ " : line.kind === "del" ? "- " : "  "}
                    {line.text}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
