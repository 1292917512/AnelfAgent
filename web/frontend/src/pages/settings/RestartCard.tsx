import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Hammer, RefreshCw } from "lucide-react";
import { systemApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { Button } from "@/components/ui";

type Phase = "idle" | "restarting" | "building" | "failed";

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** 轮询 /health 直到服务恢复（服务先断开属预期，恢复后由调用方刷新页面） */
async function waitForServiceBack(): Promise<void> {
  await sleep(3000);
  for (let i = 0; i < 90; i++) {
    try {
      const resp = await fetch("/health", { cache: "no-store" });
      if (resp.ok) return;
    } catch {
      // 服务尚未恢复，继续等待
    }
    await sleep(2000);
  }
}

/** 服务控制卡片：重启服务 / 构建前端并重启（依赖 start.sh / start.bat 的退出码 42 重启循环） */
export function RestartCard() {
  const { t } = useTranslation("settings");
  const [phase, setPhase] = useState<Phase>("idle");
  const [errorLog, setErrorLog] = useState<string>("");
  const busyRef = useRef(false);

  const waitAndReload = async () => {
    await waitForServiceBack();
    window.location.reload();
  };

  const fail = (log: string) => {
    setPhase("failed");
    setErrorLog(log);
    busyRef.current = false;
  };

  const onRestart = async () => {
    if (busyRef.current || !window.confirm(t("restartConfirm"))) return;
    busyRef.current = true;
    setPhase("restarting");
    setErrorLog("");
    try {
      await systemApi.restart();
    } catch {
      // 服务可能已开始关闭，忽略请求错误
    }
    await waitAndReload();
  };

  const onBuildRestart = async () => {
    if (busyRef.current || !window.confirm(t("buildRestartConfirm"))) return;
    busyRef.current = true;
    setPhase("building");
    setErrorLog("");
    try {
      const { data } = await systemApi.buildAndRestart();
      if (!data?.ok) {
        fail(data?.error === "build_in_progress" ? t("buildInProgress") : t("frontendNotFound"));
        return;
      }
    } catch (exc) {
      fail(String(exc));
      return;
    }
    // 轮询构建状态：构建成功服务端会自动重启，接口断开即进入等待恢复
    for (;;) {
      await sleep(3000);
      try {
        const { data: state } = await systemApi.restartStatus();
        if (!state.building && state.last) {
          if (state.last.ok) {
            setPhase("restarting");
            await waitAndReload();
          } else {
            fail(state.last.log_tail || t("buildFailed"));
          }
          return;
        }
      } catch {
        setPhase("restarting");
        await waitAndReload();
        return;
      }
    }
  };

  return (
    <Card title={t("serviceControl")} subtitle={t("serviceControlDesc")}>
      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant="danger"
          size="sm"
          onClick={onRestart}
          loading={phase === "restarting"}
          disabled={phase === "building"}
        >
          <RefreshCw size={14} /> {t("restartService")}
        </Button>
        <Button
          variant="primary"
          size="sm"
          onClick={onBuildRestart}
          loading={phase === "building"}
          disabled={phase === "restarting"}
        >
          <Hammer size={14} /> {t("buildAndRestart")}
        </Button>
        {phase === "restarting" && <span className="text-xs text-muted">{t("restartingHint")}</span>}
        {phase === "building" && <span className="text-xs text-muted">{t("buildingHint")}</span>}
      </div>
      {phase === "failed" && errorLog && (
        <pre className="mt-3 text-xs font-mono text-foreground bg-elevated border border-border rounded-md p-3 overflow-auto max-h-48 whitespace-pre-wrap">
          {errorLog}
        </pre>
      )}
    </Card>
  );
}
