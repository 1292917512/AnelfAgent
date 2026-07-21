import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { memoryApi } from "@/lib/api";
import type { MemoryFileInfo } from "@/lib/types";
import { Card } from "@/components/common/Card";
import { ConfirmDialog } from "@/components/ui";
import { cn } from "@/lib/utils";
import { Save, FileText, Trash2 } from "lucide-react";

/** events 日期便签由「日记」面板管理，此处排除 */
const EVENT_PATH_RE = /memory\/events\/\d{4}-\d{2}-\d{2}\.md$/;
const MAIN_NOTE_PATH = "memory/memory.md";

type FileGroup = "knowledge" | "groups" | "others";

function classify(path: string): FileGroup {
  if (path.includes("/groups/")) return "groups";
  if (/^memory\/[^/]+\.md$/.test(path)) return "knowledge";
  return "others";
}

const GROUP_ORDER: FileGroup[] = ["knowledge", "groups", "others"];

export function NotesPanel() {
  const { t } = useTranslation("memory");
  const queryClient = useQueryClient();
  const { data: notes } = useQuery({ queryKey: ["notes"], queryFn: () => memoryApi.notes.read().then((r) => r.data) });
  const { data: files = [] } = useQuery({ queryKey: ["memoryFiles"], queryFn: () => memoryApi.files.list().then((r) => r.data) });
  const [editingPath, setEditingPath] = useState<string | null>(null);
  const [editContent, setEditContent] = useState<string>("");
  const [isMainEdit, setIsMainEdit] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);

  /** 按 知识/群组/其他 分组（排除主便签与 events 日期便签） */
  const grouped = useMemo(() => {
    const map: Record<FileGroup, MemoryFileInfo[]> = { knowledge: [], groups: [], others: [] };
    for (const f of files) {
      if (f.path === MAIN_NOTE_PATH || EVENT_PATH_RE.test(f.path)) continue;
      map[classify(f.path)].push(f);
    }
    return map;
  }, [files]);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["memoryFiles"] });
    queryClient.invalidateQueries({ queryKey: ["notes"] });
  };

  const saveMainMutation = useMutation({ mutationFn: (c: string) => memoryApi.notes.write(c), onSuccess: () => { invalidate(); setIsMainEdit(false); } });
  const saveFileMutation = useMutation({ mutationFn: ({ path, content }: { path: string; content: string }) => memoryApi.files.write(path, content), onSuccess: () => { invalidate(); setEditingPath(null); } });
  const deleteMutation = useMutation({
    mutationFn: (path: string) => memoryApi.files.delete(path),
    onSuccess: (_r, path) => {
      invalidate();
      setPendingDelete(null);
      if (editingPath === path) { setEditingPath(null); setEditContent(""); }
    },
  });

  const openFile = async (path: string) => { const r = await memoryApi.files.read(path); setEditContent(r.data.content ?? ""); setEditingPath(path); setIsMainEdit(false); };
  const openMain = () => { setEditContent(notes?.content ?? ""); setEditingPath(null); setIsMainEdit(true); };

  const renderFile = (f: MemoryFileInfo) => (
    <div
      key={f.path}
      className={cn(
        "group flex items-center gap-2 p-2 rounded-md text-sm transition-colors cursor-pointer",
        editingPath === f.path ? "bg-accent-subtle text-accent" : "text-foreground hover:bg-hover",
      )}
      onClick={() => openFile(f.path)}
    >
      <FileText size={14} className="text-muted shrink-0" />
      <div className="min-w-0 flex-1">
        <p className="truncate">{f.path.replace(/^memory\//, "")}</p>
        <p className="text-[11px] text-muted">{t("nLines", { count: Number(f.lines) })} · {f.size}</p>
      </div>
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); setPendingDelete(f.path); }}
        className="opacity-0 group-hover:opacity-100 p-1 rounded text-muted hover:text-danger transition-all shrink-0"
      >
        <Trash2 size={13} />
      </button>
    </div>
  );

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
      <Card title={t("memoryFiles")}>
        <div className="space-y-1">
          <button onClick={openMain} className={cn("w-full text-left p-2 rounded-md text-sm transition-colors flex items-center gap-2", isMainEdit ? "bg-accent-subtle text-accent" : "text-foreground hover:bg-hover")}>
            <FileText size={14} className="flex-shrink-0" />
            <div className="min-w-0"><p className="font-medium">{t("mainNote")}</p><p className="text-[11px] text-muted font-mono truncate">{notes?.path ?? MAIN_NOTE_PATH}</p></div>
          </button>
          {GROUP_ORDER.map((g) => grouped[g].length > 0 && (
            <div key={g}>
              <p className="px-2 pt-3 pb-1 text-[11px] font-medium text-muted uppercase tracking-wide">{t(`noteGroups.${g}`)}</p>
              {grouped[g].map(renderFile)}
            </div>
          ))}
        </div>
      </Card>
      <Card title={isMainEdit ? t("mainNote") : editingPath ? editingPath.split("/").pop() ?? "" : t("selectFile")} className="md:col-span-2" actions={
        (isMainEdit || editingPath) ? (
          <button onClick={() => { if (isMainEdit) saveMainMutation.mutate(editContent); else if (editingPath) saveFileMutation.mutate({ path: editingPath, content: editContent }); }}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md bg-accent text-primary-foreground hover:bg-[var(--accent-hover)] transition-all"><Save size={14} /> {t("common:save")}</button>
        ) : undefined
      }>
        {(isMainEdit || editingPath) ? (
          <textarea value={editContent} onChange={(e) => setEditContent(e.target.value)} rows={16}
            className="w-full bg-elevated border border-input rounded-md px-3 py-2 text-sm text-foreground font-mono outline-none focus:border-ring resize-y" />
        ) : (<p className="text-sm text-muted">{t("clickToEdit")}</p>)}
      </Card>

      <ConfirmDialog
        open={pendingDelete !== null}
        onClose={() => setPendingDelete(null)}
        onConfirm={() => pendingDelete && deleteMutation.mutate(pendingDelete)}
        title={t("deleteFileTitle")}
        message={t("deleteFileConfirm", { name: pendingDelete?.replace(/^memory\//, "") ?? "" })}
        confirmText={t("common:delete")}
        cancelText={t("common:cancel")}
        danger
        loading={deleteMutation.isPending}
      />
    </div>
  );
}
