import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { memoryApi } from "@/lib/api";
import type { MemoryFileInfo } from "@/lib/types";
import { Card } from "@/components/common/Card";
import { AsyncState } from "@/components/common/AsyncState";
import { ListToolbar } from "@/components/common/ListToolbar";
import { ConfirmDialog, Button } from "@/components/ui";
import { cn } from "@/lib/utils";
import { FileText, Trash2 } from "lucide-react";
import { NoteEditor, MAIN_NOTE_PATH } from "./NoteEditor";

const EVENT_PATH_RE = /memory\/events\/\d{4}-\d{2}-\d{2}\.md$/;
type FileGroup = "knowledge" | "groups" | "others";
const GROUP_ORDER: FileGroup[] = ["knowledge", "groups", "others"];
function classify(path: string): FileGroup {
  if (path.includes("/groups/")) return "groups";
  return /^memory\/[^/]+\.md$/.test(path) ? "knowledge" : "others";
}

export function NotesPanel() {
  const { t } = useTranslation("memory");
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const selected = params.get("note") ?? MAIN_NOTE_PATH;
  const select = (path: string) => setParams((current) => { const next = new URLSearchParams(current); next.set("note", path); return next; });
  const [search, setSearch] = useState("");
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const files = useQuery({ queryKey: ["memoryFiles"], queryFn: () => memoryApi.files.list().then((r) => r.data), throwOnError: false });
  const grouped: Record<FileGroup, MemoryFileInfo[]> = { knowledge: [], groups: [], others: [] };
  for (const file of files.data ?? []) {
    if (file.path === MAIN_NOTE_PATH || EVENT_PATH_RE.test(file.path) || !file.path.toLowerCase().includes(search.trim().toLowerCase())) continue;
    grouped[classify(file.path)].push(file);
  }
  const remove = useMutation({
    mutationFn: memoryApi.files.delete,
    onSuccess: () => { void client.invalidateQueries({ queryKey: ["memoryFiles"] }); setPendingDelete(null); },
  });
  const selectedExists = selected === MAIN_NOTE_PATH || files.data?.some((file) => file.path === selected);
  return <AsyncState pending={files.isPending} error={!files.data ? files.error : undefined} retry={() => void files.refetch()}>
    <div className="grid items-start gap-5 lg:grid-cols-[300px_minmax(0,1fr)]">
      <Card title={t("memoryFiles")}>
        <ListToolbar search={search} onSearch={setSearch} count={Object.values(grouped).flat().length} />
        <div className="mt-4 max-h-[60vh] overflow-auto">
          <button onClick={() => select(MAIN_NOTE_PATH)} aria-pressed={selected === MAIN_NOTE_PATH}
            className={cn("flex w-full items-center gap-2 rounded-lg p-3 text-left text-sm", selected === MAIN_NOTE_PATH ? "bg-accent-subtle text-accent" : "hover:bg-hover")}>
            <FileText size={15} />{t("mainNote")}
          </button>
          {GROUP_ORDER.map((group) => grouped[group].length > 0 && <div key={group}>
            <p className="px-3 pb-1 pt-4 text-xs font-medium text-muted">{t(`noteGroups.${group}`)}</p>
            {grouped[group].map((file) => <div key={file.path} className={cn("flex items-center rounded-lg p-1", selected === file.path ? "bg-accent-subtle text-accent" : "hover:bg-hover")}>
              <button onClick={() => select(file.path)} aria-pressed={selected === file.path} className="min-w-0 flex-1 p-2 text-left">
                <span className="block truncate text-sm">{file.path.replace(/^memory\//, "")}</span>
                <span className="text-xs text-muted">{t("nLines", { count: Number(file.lines) })} · {file.size}</span>
              </button>
              <Button size="icon" variant="ghost" title={t("common:delete")} onClick={() => setPendingDelete(file.path)}><Trash2 size={14} /></Button>
            </div>)}
          </div>)}
        </div>
      </Card>
      {selectedExists ? <NoteEditor key={selected} path={selected} /> : <Card title={t("selectFile")}><p className="text-sm text-muted">{t("clickToEdit")}</p></Card>}
    </div>
    <ConfirmDialog open={pendingDelete !== null} onClose={() => setPendingDelete(null)}
      onConfirm={() => { if (pendingDelete) remove.mutate(pendingDelete); }} title={t("deleteFileTitle")}
      message={t("deleteFileConfirm", { name: pendingDelete?.replace(/^memory\//, "") ?? "" })}
      confirmText={t("common:delete")} danger loading={remove.isPending} />
  </AsyncState>;
}
