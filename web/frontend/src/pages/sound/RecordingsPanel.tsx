import { useState } from "react";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { InlineContributions } from "@/components/extensions/ContributionSlot";
import { audioApi, apiErrorMessage } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { QueryError } from "@/components/common/AsyncState";
import { Badge, Button, ConfirmDialog, LoadingBlock } from "@/components/ui";

const PAGE_SIZE = 12;

export function RecordingsPanel() {
  const { t } = useTranslation("sound");
  const client = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const [target, setTarget] = useState<string | null>(null);
  const query = useInfiniteQuery({
    queryKey: ["audioRecordings"], initialPageParam: 0, enabled: expanded, throwOnError: false,
    queryFn: ({ pageParam }) => audioApi.recordings({ limit: PAGE_SIZE, offset: pageParam }).then((r) => r.data),
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((count, page) => count + page.items.length, 0);
      return last.items.length && loaded < last.total ? loaded : undefined;
    },
  });
  const remove = useMutation({
    mutationFn: (path: string) => audioApi.deleteRecording(path),
    onSuccess: async () => {
      setTarget(null);
      await Promise.all([client.invalidateQueries({ queryKey: ["audioRecordings"] }), client.invalidateQueries({ queryKey: ["audioStatus"] })]);
    },
  });
  const recordings = query.data?.pages.flatMap((page) => page.items) ?? [];
  return <Card title={t("recordings.title")} subtitle={t("recordings.hint")}
    actions={<Button size="sm" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{t(expanded ? "recordings.collapse" : "recordings.browse")}</Button>}>
    {!expanded ? <p className="text-xs text-muted">{t("recordings.onDemand")}</p> : <div className="space-y-3">
      {query.isPending && <LoadingBlock label={t("common:loading")} />}
      {query.isError && <QueryError compact error={query.error} retry={() => void query.refetch()} />}
      {query.data && <p className="text-xs text-muted">{t("recordings.count", { count: query.data.pages[0]?.total ?? 0 })}</p>}
      {recordings.map((recording) => <div key={recording.path} className="rounded-lg border border-border p-3">
        <p className="break-all font-mono text-xs text-heading">{recording.path}</p>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Badge variant={recording.status === "done" ? "ok" : recording.status === "error" ? "danger" : "neutral"}>{t("recordings.status." + recording.status, { defaultValue: recording.status })}</Badge>
          <span className="flex-1 text-xs text-muted">{t("recordings.meta", { files: recording.file_count, segments: recording.segments })}</span>
          <InlineContributions slot="audio.recording.actions" componentProps={{ path: recording.path }} />
          <Button size="sm" variant="ghost" className="text-danger" onClick={() => { remove.reset(); setTarget(recording.path); }}>{t("recordings.delete")}</Button>
        </div>
        {recording.error && <p className="mt-2 break-words text-xs text-danger">{recording.error}</p>}
      </div>)}
      {query.isSuccess && !recordings.length && <p className="text-sm text-muted">{t("recordings.empty")}</p>}
      {query.hasNextPage && <Button size="sm" loading={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>{t("recordings.more")}</Button>}
    </div>}
    {remove.isError && <p role="alert" className="mt-3 text-sm text-danger">{apiErrorMessage(remove.error, t("messages.opFailed"))}</p>}
    <ConfirmDialog open={target !== null} onClose={() => { if (!remove.isPending) setTarget(null); }}
      onConfirm={() => { if (target) remove.mutate(target); }} title={t("recordings.deleteTitle")}
      message={t("recordings.deleteConfirm")} danger loading={remove.isPending} />
  </Card>;
}
