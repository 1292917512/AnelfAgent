/** 审批预览 — 按工具语义渲染审批内容（edit 出 diff、shell 出命令、写/删出路径列表）。
 *
 * 通用参数 pre 是兜底；能识别出结构化语义时给出对应预览（对齐 Codex 的
 * apply_patch_header「受影响路径全列」与 ZCode 的按工具分发渲染器）。
 */

import { useTranslation } from "react-i18next";
import { FileDiff, FilePlus, FileX, Terminal } from "lucide-react";
import { cn } from "@/lib/utils";

interface ParsedArgs {
  [key: string]: unknown;
}

function tryParse(raw: string): ParsedArgs | null {
  try {
    const v = JSON.parse(raw) as unknown;
    return v && typeof v === "object" ? (v as ParsedArgs) : null;
  } catch {
    return null;
  }
}

/** 统一 diff 行的极简渲染（红/绿/上下文，无行号——审批只需看清改动方向） */
function InlineDiff({ diff }: { diff: string }) {
  return (
    <div className="max-h-44 overflow-auto rounded bg-muted p-2 font-mono text-[11px] leading-relaxed">
      {diff.split("\n").map((line, i) => {
        const cls = line.startsWith("+")
          ? "text-green-600 dark:text-green-400"
          : line.startsWith("-")
            ? "text-red-600 dark:text-red-400"
            : line.startsWith("@@")
              ? "text-muted"
              : "text-foreground/80";
        return (
          <div key={i} className={cn("whitespace-pre-wrap break-all", cls)}>
            {line}
          </div>
        );
      })}
    </div>
  );
}

function PreviewRow({ icon: Icon, title, children }: {
  icon: typeof Terminal;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-1 flex items-center gap-1.5 text-xs text-muted">
        <Icon className="h-3.5 w-3.5" />
        {title}
      </div>
      {children}
    </div>
  );
}

/** 按工具类型分发审批预览；返回 null 表示走通用参数展示 */
export function ApprovalPreview({ toolName, toolArgs }: { toolName: string; toolArgs: string }) {
  const { t } = useTranslation("approvals");
  const args = tryParse(toolArgs);
  if (!args) return null;

  // 文件编辑：old/new 拼极简 diff
  if (toolName === "edit_file" || toolName === "write_file") {
    const path = String(args.file_path ?? args.path ?? "");
    const oldText = String(args.old_text ?? "");
    const newText = String(args.new_text ?? args.content ?? "");
    const diff = [
      `--- ${path}`,
      `+++ ${path}`,
      ...oldText.split("\n").map((l) => `- ${l}`),
      ...newText.split("\n").map((l) => `+ ${l}`),
    ].join("\n");
    return (
      <PreviewRow icon={FileDiff} title={t("popup.previewEdit", { path })}>
        <InlineDiff diff={diff} />
      </PreviewRow>
    );
  }

  // shell / 进程：命令全文 + 工作目录
  if (toolName === "run_shell_command" || toolName === "shell" || toolName === "bash") {
    const command = String(args.command ?? args.cmd ?? "");
    const cwd = String(args.cwd ?? args.workdir ?? "");
    return (
      <PreviewRow icon={Terminal} title={t("popup.previewShell")}>
        <pre className="max-h-40 overflow-auto rounded bg-muted p-2 font-mono text-xs text-foreground whitespace-pre-wrap break-all">
          {cwd ? `cd ${cwd}\n` : ""}{command}
        </pre>
      </PreviewRow>
    );
  }

  // 删除/新建：受影响路径列表
  if (toolName === "delete_file" || toolName === "create_file" || toolName === "mkdir") {
    const isDelete = toolName === "delete_file";
    const Icon = isDelete ? FileX : FilePlus;
    const paths = [args.file_path ?? args.path ?? args.dir]
      .filter((p) => typeof p === "string")
      .map(String);
    return (
      <PreviewRow icon={Icon} title={t(isDelete ? "popup.previewDelete" : "popup.previewCreate")}>
        <ul className="space-y-0.5 font-mono text-xs text-foreground">
          {paths.map((p, i) => (
            <li key={i} className="rounded bg-muted px-2 py-1 break-all">{p}</li>
          ))}
        </ul>
      </PreviewRow>
    );
  }

  return null;
}
