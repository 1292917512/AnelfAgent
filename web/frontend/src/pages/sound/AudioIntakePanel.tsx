import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { apiErrorMessage, audioApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { Button, Input } from "@/components/ui";

export function AudioIntakePanel({ available }: { available: boolean }) {
  const { t } = useTranslation("sound");
  const client = useQueryClient();
  const [path, setPath] = useState("");
  const analyze = useMutation({
    mutationFn: (value: string) => audioApi.analyze(value).then((r) => r.data),
    onSuccess: async () => {
      await Promise.all([client.invalidateQueries({ queryKey: ["audioStatus"] }), client.invalidateQueries({ queryKey: ["audioRecordings"] })]);
    },
  });
  return <Card title={t("analyzeTitle")} subtitle={t("analyzeHint")}>
    <form className="flex flex-col gap-3 sm:flex-row" onSubmit={(event) => { event.preventDefault(); if (available && path.trim() && !analyze.isPending) analyze.mutate(path.trim()); }}>
      <Input value={path} onChange={(event) => { setPath(event.target.value); analyze.reset(); }} disabled={analyze.isPending}
        aria-label={t("audioPath")} placeholder="workspace/uploads/voice/recording.wav" className="min-w-0 flex-1 font-mono text-xs" />
      <Button type="submit" loading={analyze.isPending} disabled={!path.trim() || !available}>{t("analyze")}</Button>
    </form>
    {!available && <p className="mt-3 text-xs text-muted">{t("analyzeUnavailable")}</p>}
    {analyze.isSuccess && <p role="status" className="mt-3 text-sm text-ok">{t("analyzeDone", { count: analyze.data.segments })}</p>}
    {analyze.isError && <p role="alert" className="mt-3 text-sm text-danger">{apiErrorMessage(analyze.error, t("analyzeFailed"))}</p>}
  </Card>;
}
