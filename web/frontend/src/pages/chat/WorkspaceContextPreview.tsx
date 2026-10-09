import { useTranslation } from "react-i18next";
import { FileCode2 } from "lucide-react";
import { useChatStore } from "@/stores/chat-store";
import { useWorkbenchStore } from "@/stores/workbench-store";
import { captureWorkspaceContext } from "@/lib/workspace-context";

export function WorkspaceContextPreview() {
  const { t } = useTranslation("chat");
  const chatId = useChatStore((state) => state.activeChatId);
  const enabled = useChatStore((state) => state.buckets[chatId]?.workspaceContextEnabled ?? true);
  const setEnabled = useChatStore((state) => state.setWorkspaceContextEnabled);
  useWorkbenchStore((state) => state.openFiles);
  useWorkbenchStore((state) => state.activeFileId);
  useWorkbenchStore((state) => state.selection);
  const context = captureWorkspaceContext();
  if (!context.open_tabs.length) return null;
  return <div className="mb-2 rounded-lg border border-border bg-elevated/50 px-3 py-2 text-xs">
    <div className="flex flex-wrap items-center gap-2">
      <FileCode2 size={14} className="shrink-0 text-muted" />
      <label className="flex cursor-pointer items-center gap-2">
        <input type="checkbox" checked={enabled} onChange={(event) => setEnabled(chatId, event.target.checked)} />
        {t("workspaceContext.include")}
      </label>
      <span className="min-w-0 truncate text-muted">{t("workspaceContext.summary", { tabs: context.open_tabs.length, chars: context.selection?.content.length ?? 0 })}</span>
    </div>
    <details className="mt-2 text-muted">
      <summary className="cursor-pointer">{t("workspaceContext.preview")}</summary>
      <p className="my-2 leading-5">{enabled ? t("workspaceContext.help") : t("workspaceContext.excluded")}</p>
      {context.active_file && <p className="mb-2 break-all">{t("workspaceContext.active")}: {context.active_file}</p>}
      <ul className="max-h-24 space-y-1 overflow-auto">{context.open_tabs.map((tab) => <li key={tab.path} className="break-all">{tab.path}</li>)}</ul>
      {context.selection && <div className="mt-2">
        <p className="mb-1">{t("workspaceContext.selection")}: {context.selection.ranges.map((range) => `${range.start_line}–${range.end_line}`).join(", ")}</p>
        <pre className="max-h-36 overflow-auto whitespace-pre-wrap break-words rounded-md bg-card p-2 text-foreground">{context.selection.content}</pre>
      </div>}
    </details>
  </div>;
}
