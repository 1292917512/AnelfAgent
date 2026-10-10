import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, ConfirmDialog, toast } from "@/components/ui";
import { audiosyncApi } from "./api";
export default function RebuildRecording({ path }: { path: string }) {
  const { t } = useTranslation("audiosync");
  const queryClient = useQueryClient();
  const [rebuildTarget, setRebuildTarget] = useState<string | null>(null);
  const rebuildMut = useMutation({
    mutationFn: (p: string) => audiosyncApi.rebuildRecordings([p]).then((r) => r.data),
    onSuccess: (data) => {
      const outcome = data.results?.[0]?.outcome ?? "error";
      if (data.error || outcome === "error") {
        toast.error(data.error || t("operationFailed"));
        return;
      }
      toast.success(t(`recordings.outcome.${outcome}`, { defaultValue: outcome }));
      setRebuildTarget(null);
      queryClient.invalidateQueries({ queryKey: ["audioRecordings"] });
      queryClient.invalidateQueries({ queryKey: ["audioStatus"] });
      queryClient.invalidateQueries({ queryKey: ["audioTimeline"] });
      queryClient.invalidateQueries({ queryKey: ["audioSegments"] });
    },
    onError: (e: unknown) => {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail || t("operationFailed"));
    },
  });

  return <>
    <Button size="sm" variant="secondary" className="shrink-0" disabled={rebuildMut.isPending} onClick={() => setRebuildTarget(path)}>{t("recordings.rebuild")}</Button>
      <ConfirmDialog
        open={rebuildTarget !== null}
        onClose={() => setRebuildTarget(null)}
        onConfirm={() => rebuildTarget && rebuildMut.mutate(rebuildTarget)}
        title={t("recordings.rebuildTitle")}
        message={t("recordings.rebuildConfirm")}
        danger
        loading={rebuildMut.isPending}
      />
  </>;
}
