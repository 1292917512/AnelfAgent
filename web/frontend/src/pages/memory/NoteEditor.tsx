import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Save, RotateCcw } from "lucide-react";
import { memoryApi } from "@/lib/api";
import { useDraft } from "@/hooks/useDraft";
import { useUnsavedChanges } from "@/components/common/UnsavedChanges";
import { AsyncState } from "@/components/common/AsyncState";
import { Card } from "@/components/common/Card";
import { Button, Textarea } from "@/components/ui";

export const MAIN_NOTE_PATH = "memory/memory.md";
export function NoteEditor({ path }: { path: string }) {
  const query = useQuery({
    queryKey: ["note-content", path],
    queryFn: () => (path === MAIN_NOTE_PATH ? memoryApi.notes.read() : memoryApi.files.read(path)).then((r) => r.data.content),
    throwOnError: false,
  });
  return <AsyncState pending={query.isPending} error={query.error} retry={() => void query.refetch()}>
    {query.data !== undefined && <Editor path={path} content={query.data} />}
  </AsyncState>;
}

function Editor({ path, content }: { path: string; content: string }) {
  const { t } = useTranslation("memory");
  const client = useQueryClient();
  const draft = useDraft({ content });
  useUnsavedChanges(draft.dirty);
  const save = useMutation({
    mutationFn: async (submitted: string) => {
      if (path === MAIN_NOTE_PATH) await memoryApi.notes.write(submitted);
      else await memoryApi.files.write(path, submitted);
    },
    onSuccess: (_response, submitted) => {
      client.setQueryData(["note-content", path], submitted);
      draft.acknowledge({ content: submitted });
      void client.invalidateQueries({ queryKey: ["memoryFiles"] });
      void client.invalidateQueries({ queryKey: ["notes"] });
    },
  });
  return <Card title={path === MAIN_NOTE_PATH ? t("mainNote") : path.replace(/^memory\//, "")} actions={
    <div className="flex items-center gap-2">
      <Button size="icon" title={t("common:reset")} onClick={draft.reset} disabled={!draft.dirty || save.isPending}><RotateCcw size={14} /></Button>
      <Button variant="primary" size="sm" loading={save.isPending} disabled={!draft.dirty}
        onClick={() => save.mutate(draft.values.content)}><Save size={14} />{t("common:save")}</Button>
    </div>
  }>
    <Textarea aria-label={path} value={draft.values.content} onChange={(event) => draft.update("content", event.target.value)}
      rows={20} className="w-full min-h-[45vh] resize-y font-mono text-sm leading-relaxed" />
    <p className="mt-2 text-xs text-muted" role="status">{draft.dirty ? t("common:unsavedChanges") : save.isSuccess ? t("common:saved") : path}</p>
  </Card>;
}
