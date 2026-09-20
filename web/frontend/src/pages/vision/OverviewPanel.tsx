import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { visionApi } from "@/lib/api";
import type { VisionSourceStatus } from "@/lib/types";
import { formatAge } from "@/lib/utils";
import { Badge, Button, LoadingBlock, Switch, toast } from "@/components/ui";
import { Camera, Eye, MonitorPlay, Play, Radio, Square } from "lucide-react";

/** 视觉总览：视觉源清单 + 监视开关 + 最新画面预览 */
export function VisionOverviewPanel() {
  const { t } = useTranslation("vision");
  const queryClient = useQueryClient();
  const [previewTick, setPreviewTick] = useState(0);

  const { data: status, isLoading } = useQuery({
    queryKey: ["visionStatus"],
    queryFn: () => visionApi.status().then((r) => r.data),
    refetchInterval: 5000,
  });
  const { data: sources } = useQuery({
    queryKey: ["visionSources"],
    queryFn: () => visionApi.sources().then((r) => r.data.sources),
  });

  const watchMut = useMutation({
    mutationFn: ({ action, source }: { action: string; source: string }) =>
      visionApi.watch(action, source).then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["visionStatus"] });
      queryClient.invalidateQueries({ queryKey: ["visionSources"] });
    },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      toast.error(detail || t("watchFailed"));
    },
  });

  if (isLoading || !status) return <LoadingBlock label={t("common:loading")} />;

  const watching = new Set(status.watching);
  const injection = status.injection;

  return (
    <div className="space-y-4 max-w-4xl">
      {/* 上下文注入情况 */}
      <div className="rounded-md border border-border bg-card px-4 py-3 flex flex-wrap items-center gap-2 text-xs">
        <Radio size={14} className="text-accent" />
        <span className="text-sm font-semibold text-heading">{t("injection.title")}</span>
        <Badge variant={injection?.active ? "ok" : "neutral"}>
          {injection?.active ? t("injection.active") : t("injection.inactive")}
        </Badge>
        {injection?.last_media_inject && (
          <span className="text-muted">
            {t("injection.lastMedia")}: {injection.last_media_inject.source} ·{" "}
            {(injection.last_media_inject.at ? formatAge(injection.last_media_inject.at) : "-")}
          </span>
        )}
        {injection?.last_text_inject_at ? (
          <span className="text-muted">
            {t("injection.lastText")}: {(injection.last_text_inject_at ? formatAge(injection.last_text_inject_at) : "-")}
          </span>
        ) : null}
      </div>

      {/* 最新画面预览 */}
      <div className="rounded-md border border-border bg-card p-4 space-y-3">
        <div className="flex items-center gap-2">
          <Camera size={15} className="text-accent" />
          <span className="text-sm font-semibold text-heading">{t("latestFrame")}</span>
          <span className="ml-auto text-xs text-muted">
            {status.latest
              ? `${status.latest.source} · ${(status.latest.captured_at ? formatAge(status.latest.captured_at) : "-")}`
              : t("noFrame")}
          </span>
          <Button size="sm" variant="ghost" onClick={() => setPreviewTick(Date.now())}>
            {t("refresh")}
          </Button>
        </div>
        {status.latest ? (
          <img
            src={`${visionApi.latestUrl()}&t=${previewTick}`}
            alt={t("latestFrame")}
            className="w-full max-w-xl rounded-md border border-border"
          />
        ) : (
          <p className="text-xs text-muted">{t("noFrameHint")}</p>
        )}
      </div>

      {/* 视觉源清单 */}
      <div className="rounded-md border border-border bg-card p-4 space-y-2">
        <div className="flex items-center gap-2">
          <Eye size={15} className="text-accent" />
          <span className="text-sm font-semibold text-heading">{t("sources")}</span>
        </div>
        <div className="space-y-1.5">
          {(sources ?? []).map((src) => {
            const st: VisionSourceStatus = status.sources?.[src.key] ?? {};
            const active = watching.has(src.key);
            const enabled = src.enabled !== false;
            return (
              <div
                key={src.key}
                className={`flex items-center gap-3 rounded-md bg-elevated px-3 py-2 ${
                  enabled ? "" : "opacity-55"
                }`}
              >
                <MonitorPlay size={14} className="text-muted shrink-0" />
                <div className="min-w-0">
                  <div className="text-sm text-foreground">
                    {src.display_name}
                    <span className="ml-2 text-[11px] font-mono text-muted">{src.key}</span>
                    {!enabled && (
                      <Badge variant="neutral" className="ml-2">{t("sourceDisabled")}</Badge>
                    )}
                    {active && (
                      <Badge variant="ok" className="ml-2">{t("watching")}</Badge>
                    )}
                  </div>
                  <div className="text-[11px] text-muted truncate">
                    {src.description}
                    {st.capture_count ? ` · ${t("captures", { count: st.capture_count })}` : ""}
                    {st.last_capture_at
                      ? ` · ${t("lastCapture")} ${(st.last_capture_at ? formatAge(st.last_capture_at) : "-")}`
                      : ""}
                    {st.last_error ? ` · ⚠ ${st.last_error}` : ""}
                  </div>
                </div>
                <div className="ml-auto flex items-center gap-2 shrink-0">
                  {enabled && src.pollable &&
                    (active ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => watchMut.mutate({ action: "stop", source: src.key })}
                      >
                        <Square size={12} className="mr-1" />
                        {t("stopWatch")}
                      </Button>
                    ) : (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => watchMut.mutate({ action: "start", source: src.key })}
                      >
                        <Play size={12} className="mr-1" />
                        {t("startWatch")}
                      </Button>
                    ))}
                  {enabled && !src.pollable && (
                    <span className="text-[11px] text-muted">{t("externalSource")}</span>
                  )}
                  <Switch
                    checked={enabled}
                    onChange={(v) =>
                      watchMut.mutate({ action: v ? "enable" : "disable", source: src.key })}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
