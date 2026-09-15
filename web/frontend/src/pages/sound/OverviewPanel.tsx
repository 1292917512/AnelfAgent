import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { audioApi, configMetaApi, type AudioStatus } from "@/lib/api";
import { Badge, Button, Input, LoadingBlock, toast } from "@/components/ui";
import { ProviderKeysPanel } from "@/components/common/ProviderKeysPanel";
import { AudioLines, CheckCircle2, Database, FileAudio, Mic, Phone, Radio, ServerCog, XCircle } from "lucide-react";

/** 音频总览：提供者链状态 + FunASR 服务 + 音频库统计 + 上下文注入情况 + 文件解析入库 */
export function AudioOverviewPanel() {
  const { t } = useTranslation("sound");
  const queryClient = useQueryClient();
  const [path, setPath] = useState("");
  const [funasrDraft, setFunasrDraft] = useState<string | null>(null);

  const { data: status, isLoading } = useQuery({
    queryKey: ["audioStatus"],
    queryFn: () => audioApi.status().then((r) => r.data as AudioStatus),
    refetchInterval: 8000,
  });
  const { data: funasr } = useQuery({
    queryKey: ["funasrStatus"],
    queryFn: () => audioApi.funasrStatus().then((r) => r.data),
  });
  const funasrSave = useMutation({
    mutationFn: (v: string) => configMetaApi.save("funasr_endpoint", v.trim()),
    onSuccess: () => {
      setFunasrDraft(null);
      toast.success(t("common:saveSuccess"));
      queryClient.invalidateQueries({ queryKey: ["funasrStatus"] });
      queryClient.invalidateQueries({ queryKey: ["audioStatus"] });
    },
    onError: () => toast.error(t("common:saveFailed")),
  });
  const funasrCheck = useMutation({
    mutationFn: () => audioApi.funasrStatus(true).then((r) => r.data),
    onSuccess: (data) => {
      queryClient.setQueryData(["funasrStatus"], data);
      toast[data.reachable ? "success" : "error"](
        data.reachable ? t("funasr.reachable") : t("funasr.unreachable"));
    },
  });

  const analyzeMut = useMutation({
    mutationFn: (p: string) => audioApi.analyze(p).then((r) => r.data),
    onSuccess: (data) => {
      toast.success(t("analyzeDone", { count: (data as { segments?: number }).segments ?? 0 }));
      queryClient.invalidateQueries({ queryKey: ["audioStatus"] });
    },
    onError: (e: unknown) => {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail || t("analyzeFailed"));
    },
  });

  if (isLoading || !status) return <LoadingBlock label={t("common:loading")} />;

  const lib = status.library ?? {};
  const audioMinutes = Math.round((lib.audio_ms ?? 0) / 6000) / 10;
  const injection = status.injection;

  return (
    <div className="space-y-4 max-w-3xl">
      {/* 提供者链 */}
      <div className="rounded-md border border-border bg-card p-4 space-y-2">
        <div className="flex items-center gap-2">
          <AudioLines size={15} className="text-accent" />
          <span className="text-sm font-semibold text-heading">{t("providers")}</span>
          <span className="ml-auto text-xs text-muted">
            {t("asr")}: {status.asr_available ? t("ready") : t("notConfigured")} ·{" "}
            {t("voiceprint")}: {status.voiceprint_available ? t("ready") : t("notConfigured")}
          </span>
        </div>
        <div className="space-y-1.5">
          {status.providers.map((p, i) => (
            <div key={`${p.kind}/${p.name}`}>
              {(i === 0 || status.providers[i - 1]?.kind !== p.kind) && (
                <p className="text-[11px] font-medium text-muted mt-2 mb-0.5">
                  {t(`kind.${p.kind}`, { defaultValue: p.kind })}
                </p>
              )}
              <div className="flex items-center gap-3 rounded-md bg-elevated px-3 py-2">
                {p.available ? (
                  <CheckCircle2 size={14} className="text-green-500 shrink-0" />
                ) : (
                  <XCircle size={14} className="text-muted shrink-0" />
                )}
                <span className="text-sm text-foreground">{p.name}</span>
                <span className="ml-auto text-[11px] text-muted">priority {p.priority}</span>
              </div>
              {!p.available && p.hint && (
                <p className="text-[11px] text-muted mt-0.5 ml-6">{p.hint}</p>
              )}
            </div>
          ))}
          {status.providers.length === 0 && (
            <p className="text-xs text-muted">{t("noProviders")}</p>
          )}
        </div>
      </div>

      {/* FunASR 转写服务（声音域自管：状态 + 地址配置就地闭环） */}
      <div className="rounded-md border border-border bg-card p-4 space-y-2">
        <div className="flex items-center gap-2">
          <ServerCog size={15} className="text-accent" />
          <span className="text-sm font-semibold text-heading">{t("funasr.title")}</span>
          {funasr && (
            <Badge variant={funasr.reachable ? "ok" : funasr.configured ? "danger" : "neutral"}>
              {funasr.reachable
                ? t("funasr.reachable")
                : funasr.configured ? t("funasr.unreachable") : t("funasr.notConfigured")}
            </Badge>
          )}
          <Button
            size="sm"
            className="ml-auto"
            loading={funasrCheck.isPending}
            onClick={() => funasrCheck.mutate()}
          >
            {t("funasr.recheck")}
          </Button>
        </div>
        <p className="text-xs text-muted">{t("funasr.desc")}</p>
        <div className="flex items-center gap-2">
          <Input
            className="flex-1 font-mono text-xs"
            placeholder={t("funasr.placeholder")}
            value={funasrDraft ?? funasr?.endpoint ?? ""}
            onChange={(e) => setFunasrDraft(e.target.value)}
          />
          <Button
            size="sm"
            variant="primary"
            disabled={funasrDraft === null || funasrSave.isPending}
            loading={funasrSave.isPending}
            onClick={() => funasrDraft !== null && funasrSave.mutate(funasrDraft)}
          >
            {t("funasr.save")}
          </Button>
        </div>
      </div>

      {/* TTS 提供者链 */}
      <div className="rounded-md border border-border bg-card p-4 space-y-2">
        <div className="flex items-center gap-2">
          <Mic size={15} className="text-accent" />
          <span className="text-sm font-semibold text-heading">{t("ttsProviders")}</span>
          <span className="ml-auto text-xs text-muted">
            {status.tts_available ? t("ready") : t("notConfigured")}
          </span>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {(status.tts_providers ?? []).map((p) => (
            <Badge key={p.name} variant={p.available ? "ok" : "neutral"}>
              {p.name} {p.available ? "✓" : "✗"}
            </Badge>
          ))}
          {(status.tts_providers ?? []).length === 0 && (
            <p className="text-xs text-muted">{t("noProviders")}</p>
          )}
        </div>
      </div>

      {/* 组件凭据（外部平台 Key 集中配置） */}
      <ProviderKeysPanel domain="sound" />

      {/* 实时语音状态 */}
      <div className="rounded-md border border-border bg-card p-4 space-y-2">
        <div className="flex items-center gap-2">
          <Phone size={15} className="text-accent" />
          <span className="text-sm font-semibold text-heading">{t("realtimeStatus")}</span>
          <Badge variant={status.realtime?.enabled ? "ok" : "neutral"}>
            {status.realtime?.mode ?? "cascade"}
          </Badge>
          <span className="ml-auto text-xs text-muted">
            {t("realtimeSessions", { count: status.realtime?.sessions ?? 0 })}
          </span>
        </div>
        {(status.realtime?.sessions ?? 0) > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(status.realtime?.states ?? {}).map(([owner, st]) => (
              <Badge key={owner} variant="accent">{st}</Badge>
            ))}
          </div>
        )}
      </div>

      {/* 音频库统计 */}
      <div className="rounded-md border border-border bg-card p-4 space-y-2">
        <div className="flex items-center gap-2">
          <Database size={15} className="text-accent" />
          <span className="text-sm font-semibold text-heading">{t("libraryStats")}</span>
        </div>
        <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
          <span className="text-muted">{t("segments")}: <b className="text-foreground">{lib.segments ?? 0}</b></span>
          <span className="text-muted">{t("speakers")}: <b className="text-foreground">{lib.speakers ?? 0}</b></span>
          <span className="text-muted">{t("stats.pending")}: <b className="text-foreground">{lib.pending_speakers ?? 0}</b></span>
          <span className="text-muted">{t("stats.bound")}: <b className="text-foreground">{lib.bound_speakers ?? 0}</b></span>
          <span className="text-muted">{t("stats.unread")}: <b className="text-foreground">{lib.unread_segments ?? 0}</b></span>
          <span className="text-muted">{t("audioMinutes")}: <b className="text-foreground">{audioMinutes}</b></span>
        </div>
        {lib.db_path && <p className="text-[11px] font-mono text-muted break-all">{lib.db_path}</p>}
      </div>

      {/* 上下文注入情况 */}
      <div className="rounded-md border border-border bg-card p-4 space-y-2">
        <div className="flex items-center gap-2">
          <Radio size={15} className="text-accent" />
          <span className="text-sm font-semibold text-heading">{t("injection.title")}</span>
          <Badge variant={injection?.active ? "ok" : "neutral"}>
            {injection?.active ? t("injection.active") : t("injection.inactive")}
          </Badge>
        </div>
        <p className="text-xs text-muted">{t("injection.hint")}</p>
      </div>

      {/* 文件解析入库 */}
      <div className="rounded-md border border-border bg-card p-4 space-y-3">
        <div className="flex items-center gap-2">
          <FileAudio size={15} className="text-accent" />
          <span className="text-sm font-semibold text-heading">{t("analyzeTitle")}</span>
        </div>
        <p className="text-xs text-muted">{t("analyzeHint")}</p>
        <div className="flex gap-2">
          <Input
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="workspace/uploads/voice/xxx.wav"
            className="flex-1 font-mono text-xs"
          />
          <Button
            size="sm"
            disabled={!path.trim() || analyzeMut.isPending || !status.asr_available}
            onClick={() => analyzeMut.mutate(path.trim())}
          >
            {t("analyze")}
          </Button>
        </div>
      </div>
    </div>
  );
}
