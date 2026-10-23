import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Pause, Play } from "lucide-react";
import { useTranslation } from "react-i18next";
import { memoryApi } from "@/lib/api";
import { QueryError } from "@/components/common/AsyncState";
import type { CogneeStatus } from "@/lib/types";

export function SyncControl({ sync, enabled, stale }: {
  sync: CogneeStatus["sync"]; enabled: boolean; stale: boolean;
}) {
  const { t } = useTranslation("memory");
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: (resume: boolean) => memoryApi.cognee.saveConfig({ sync_enabled: resume }),
    onSuccess: async () => {
      await Promise.all(["cogneeStatus", "cogneeConfig", "memoryHealth"].map((key) =>
        queryClient.invalidateQueries({ queryKey: [key] })));
    },
  });
  const waiting = sync.pause_requested || (mutation.isPending && mutation.variables === false);
  return <div className="space-y-2">
    <button type="button" disabled={!enabled || stale || mutation.isPending || !!sync.pause_requested}
      onClick={() => mutation.mutate(!sync.enabled)}
      className="flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs font-medium text-heading hover:bg-elevated disabled:opacity-50">
      {sync.enabled ? <Pause size={14} /> : <Play size={14} />}
      {t(waiting ? "cognee.progress.pausing" : sync.enabled ? "cognee.progress.pause" : "cognee.progress.resume")}
    </button>
    {mutation.error && <QueryError error={mutation.error} compact />}
  </div>;
}
