import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge, Button, ConfirmDialog, toast } from "@/components/ui";
import { ServerCog, Zap } from "lucide-react";
import { audiosyncApi } from "./api";
const GPU_WORKERS = ["moss", "asr", "diarize", "embedder"] as const;

export default function ServiceStatus() {
  const { t } = useTranslation("audiosync");
  const queryClient = useQueryClient();
  const { data: funasr } = useQuery({
    queryKey: ["funasrStatus"],
    queryFn: () => audiosyncApi.funasrStatus().then((r) => r.data),
  });
  const funasrCheck = useMutation({
    mutationFn: () => audiosyncApi.funasrStatus(true).then((r) => r.data),
    onSuccess: (data) => {
      queryClient.setQueryData(["funasrStatus"], data);
      toast[data.reachable ? "success" : "error"](
        data.reachable ? t("funasr.reachable") : t("funasr.unreachable"));
    },
  });

  const gpuQuery = useQuery({
    queryKey: ["funasrGpu"],
    queryFn: () => audiosyncApi.gpuStatus().then((r) => r.data),
    enabled: Boolean(funasr?.reachable),
    refetchInterval: 15_000,
    retry: false,
  });
  const [unloadAllOpen, setUnloadAllOpen] = useState(false);
  const gpuUnload = useMutation({
    mutationFn: (targets?: string[]) => audiosyncApi.gpuUnload(targets).then((r) => r.data),
    onSuccess: (data) => {
      setUnloadAllOpen(false);
      const failed = Object.entries(data).filter(([, v]) => v.error);
      if (failed.length) {
        toast.error(failed.map(([k, v]) => `${k}: ${v.error}`).join("; "));
      } else {
        toast.success(t("gpu.unloaded"));
      }
      queryClient.invalidateQueries({ queryKey: ["funasrGpu"] });
    },
    onError: (e: unknown) => {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail || t("operationFailed"));
    },
  });

  return <>
      {/* FunASR 转写服务（只读状态；地址配置在 模型页 → 组件凭据） */}
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
        <div className="flex items-center gap-2 flex-wrap text-xs">
          <span className="text-muted">{t("funasr.desc")}</span>
          <a href="/webui/models" className="text-accent hover:underline">
            {t("funasr.goConfigure")}
          </a>
        </div>
        {funasr?.endpoint && (
          <p className="text-[11px] font-mono text-muted break-all">{funasr.endpoint}</p>
        )}
        <p className="text-[11px] text-muted">{t("funasr.modelNote")}</p>
      </div>

      {/* GPU 模型显存（voicehub 模型层启停；释放后下次推理自动重载） */}
      <div className="rounded-md border border-border bg-card p-4 space-y-2">
        <div className="flex items-center gap-2">
          <Zap size={15} className="text-accent" />
          <span className="text-sm font-semibold text-heading">{t("gpu.title")}</span>
          <Button
            size="sm"
            variant="secondary"
            className="ml-auto"
            disabled={gpuQuery.isError || gpuUnload.isPending}
            onClick={() => setUnloadAllOpen(true)}
          >
            {t("gpu.unloadAll")}
          </Button>
        </div>
        <p className="text-xs text-muted">{t("gpu.hint")}</p>
        {gpuQuery.isError ? (
          <p className="text-xs text-muted">{t("gpu.unsupported")}</p>
        ) : (
          <div className="space-y-1.5">
            {GPU_WORKERS.map((key) => {
              const worker = gpuQuery.data?.[key];
              const loaded = Boolean(worker?.loaded);
              return (
                <div key={key} className="flex items-center gap-3 rounded-md bg-elevated px-3 py-2">
                  <span className="text-sm text-foreground">{t(`gpu.worker.${key}`)}</span>
                  {worker?.model && (
                    <span className="text-[11px] text-muted font-mono">{worker.model}</span>
                  )}
                  {typeof worker?.vram_gb === "number" && worker.vram_gb > 0 && (
                    <span className="text-[11px] text-muted">{worker.vram_gb.toFixed(1)} GB</span>
                  )}
                  <Badge variant={loaded ? "ok" : "neutral"}>
                    {loaded ? t("gpu.loaded") : t("gpu.released")}
                  </Badge>
                  {loaded && (
                    <Button
                      size="sm"
                      variant="ghost"
                      className="ml-auto"
                      loading={gpuUnload.isPending}
                      onClick={() => gpuUnload.mutate([key])}
                    >
                      {t("gpu.release")}
                    </Button>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      <ConfirmDialog
        open={unloadAllOpen}
        onClose={() => setUnloadAllOpen(false)}
        onConfirm={() => gpuUnload.mutate(undefined)}
        title={t("gpu.unloadAllTitle")}
        message={t("gpu.unloadAllConfirm")}
        danger
        loading={gpuUnload.isPending}
      />
  </>;
}
