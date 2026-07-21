import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { memoryApi } from "@/lib/api";
import type { MemoryDocument } from "@/lib/types";
import { Card } from "@/components/common/Card";
import { ConfirmDialog } from "@/components/ui";
import { FileText, Loader2, Trash2, Upload } from "lucide-react";

const ACCEPT = ".pdf,.docx,.txt,.md";

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function DocsPanel() {
  const { t } = useTranslation("memory");
  const queryClient = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [pendingDelete, setPendingDelete] = useState<MemoryDocument | null>(null);
  const [uploadError, setUploadError] = useState("");

  const { data: docs = [] } = useQuery({
    queryKey: ["memoryDocs"],
    queryFn: () => memoryApi.documents.list().then((r) => r.data),
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["memoryDocs"] });
    queryClient.invalidateQueries({ queryKey: ["indexStatus"] });
  };

  const uploadMutation = useMutation({
    mutationFn: async (file: File) => {
      const r = await memoryApi.documents.upload(file);
      if (r.data?.error) throw new Error(r.data.error);
    },
    onSuccess: () => { setUploadError(""); invalidate(); },
    onError: (e: Error) => setUploadError(e.message),
  });

  const deleteMutation = useMutation({
    mutationFn: (path: string) => memoryApi.documents.delete(path),
    onSuccess: () => { setPendingDelete(null); invalidate(); },
  });

  const onPick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) uploadMutation.mutate(file);
    e.target.value = "";
  };

  return (
    <Card
      title={t("docs.title")}
      actions={
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={uploadMutation.isPending}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md bg-accent text-primary-foreground hover:bg-[var(--accent-hover)] transition-all disabled:opacity-50"
        >
          {uploadMutation.isPending ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
          {uploadMutation.isPending ? t("docs.uploading") : t("docs.upload")}
        </button>
      }
    >
      <input ref={inputRef} type="file" accept={ACCEPT} className="hidden" onChange={onPick} />
      {uploadError && <p className="mb-3 text-xs text-danger">{uploadError}</p>}
      {docs.length === 0 ? (
        <p className="text-sm text-muted">{t("docs.empty")}</p>
      ) : (
        <div className="space-y-1">
          {docs.map((d) => (
            <div key={d.path} className="group flex items-center gap-2 p-2 rounded-md text-sm text-foreground hover:bg-hover transition-colors">
              <FileText size={14} className="text-muted shrink-0" />
              <div className="min-w-0 flex-1">
                <p className="truncate">{d.name}</p>
                <p className="text-[11px] text-muted">
                  {formatSize(d.size)} · {t("docs.chunks", { count: d.chunks })} · {new Date(d.indexed_at * 1000).toLocaleString()}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setPendingDelete(d)}
                className="opacity-0 group-hover:opacity-100 p-1 rounded text-muted hover:text-danger transition-all shrink-0"
              >
                <Trash2 size={13} />
              </button>
            </div>
          ))}
        </div>
      )}

      <ConfirmDialog
        open={pendingDelete !== null}
        onClose={() => setPendingDelete(null)}
        onConfirm={() => pendingDelete && deleteMutation.mutate(pendingDelete.path)}
        title={t("docs.deleteTitle")}
        message={t("docs.deleteConfirm", { name: pendingDelete?.name ?? "" })}
        confirmText={t("common:delete")}
        cancelText={t("common:cancel")}
        danger
        loading={deleteMutation.isPending}
      />
    </Card>
  );
}
