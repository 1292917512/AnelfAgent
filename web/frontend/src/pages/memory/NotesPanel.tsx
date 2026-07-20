import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { memoryApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { cn } from "@/lib/utils";
import { Save, FileText } from "lucide-react";

export function NotesPanel() {
  const { t } = useTranslation("memory");
  const queryClient = useQueryClient();
  const { data: notes } = useQuery({ queryKey: ["notes"], queryFn: () => memoryApi.notes.read().then((r) => r.data) });
  const { data: files = [] } = useQuery({ queryKey: ["memoryFiles"], queryFn: () => memoryApi.files.list().then((r) => r.data) });
  const [editingPath, setEditingPath] = useState<string | null>(null);
  const [editContent, setEditContent] = useState<string>("");
  const [isMainEdit, setIsMainEdit] = useState(false);

  const saveMainMutation = useMutation({ mutationFn: (c: string) => memoryApi.notes.write(c), onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["notes"] }); setIsMainEdit(false); } });
  const saveFileMutation = useMutation({ mutationFn: ({ path, content }: { path: string; content: string }) => memoryApi.files.write(path, content), onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["memoryFiles"] }); setEditingPath(null); } });

  const openFile = async (path: string) => { const r = await memoryApi.files.read(path); setEditContent(r.data.content ?? ""); setEditingPath(path); setIsMainEdit(false); };
  const openMain = () => { setEditContent(notes?.content ?? ""); setEditingPath(null); setIsMainEdit(true); };

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
      <Card title={t("memoryFiles")}>
        <div className="space-y-1">
          <button onClick={openMain} className={cn("w-full text-left p-2 rounded-md text-sm transition-colors flex items-center gap-2", isMainEdit ? "bg-accent-subtle text-accent" : "text-foreground hover:bg-hover")}>
            <FileText size={14} className="flex-shrink-0" />
            <div className="min-w-0"><p className="font-medium">{t("mainNote")}</p><p className="text-[11px] text-muted font-mono truncate">{notes?.path ?? "memory.md"}</p></div>
          </button>
          {files.map((f: Record<string, string>) => (
            <button key={f.path ?? f.name} onClick={() => openFile(f.path ?? f.name ?? "")} className={cn("w-full text-left p-2 rounded-md text-sm transition-colors flex items-center gap-2", editingPath === (f.path ?? f.name) ? "bg-accent-subtle text-accent" : "text-foreground hover:bg-hover")}>
              <FileText size={14} className="text-muted flex-shrink-0" />
              <div className="min-w-0"><p className="truncate">{f.name ?? f.path}</p><p className="text-[11px] text-muted">{f.lines ? t("nLines", { count: Number(f.lines) }) : ""}{f.size ? ` · ${f.size}` : ""}</p></div>
            </button>
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
    </div>
  );
}
