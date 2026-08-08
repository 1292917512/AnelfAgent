/**
 * devops 实体自定义面板 — 服务控制与项目更新。
 *
 * 通过 scripts/link_entity_panels.py 软链接到前端 panels 目录，
 * Vite import.meta.glob 自动发现并懒加载。
 * 与 AI 运维工具共用同一实现（/api/entity/devops → entities/devops/service.py）。
 */
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { GitBranch, Hammer, RefreshCw, Rocket } from "lucide-react";
import { devopsApi } from "@/lib/api";
import type { DevopsActionResult } from "@/lib/types";
import { Card } from "@/components/common/Card";
import { Button } from "@/components/ui";

type Phase = "idle" | "restarting" | "building" | "pulling" | "failed";

interface Notice {
  ok: boolean;
  text: string;
}

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

export default function DevopsPanel() {
  const { t } = useTranslation("devops");
  const [phase, setPhase] = useState<Phase>("idle");
  const [errorLog, setErrorLog] = useState("");
  const [notice, setNotice] = useState<Notice | null>(null);
  const busyRef = useRef(false);

  const { data: buildState } = useQuery({
    queryKey: ["devops-build-state"],
    queryFn: () => devopsApi.buildState().then((r) => r.data),
    refetchInterval: 15000,
  });

  const busy = phase === "restarting" || phase === "building" || phase === "pulling";

  const waitAndReload = async () => {
    await waitForServiceBack();
    window.location.reload();
  };

  const fail = (log: string) => {
    setPhase("failed");
    setErrorLog(log);
    busyRef.current = false;
  };

  /** 构建类动作统一流程：已进入后台构建后轮询状态，成功即进入重启等待 */
  const trackBuildAndRestart = async () => {
    for (;;) {
      await sleep(3000);
      try {
        const { data: state } = await devopsApi.buildState();
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

  const beginBuild = async (trigger: () => Promise<{ data: DevopsActionResult }>) => {
    setPhase("building");
    setErrorLog("");
    setNotice(null);
    try {
      const { data } = await trigger();
      if (!data?.ok) {
        if (data?.error === "build_in_progress") {
          // 已有构建在进行：直接并入跟踪
          await trackBuildAndRestart();
          return;
        }
        fail(data?.message || t("frontendNotFound"));
        return;
      }
    } catch (exc) {
      fail(String(exc));
      return;
    }
    await trackBuildAndRestart();
  };

  const onRestart = async () => {
    if (busyRef.current || !window.confirm(t("restartConfirm"))) return;
    busyRef.current = true;
    setPhase("restarting");
    setErrorLog("");
    setNotice(null);
    try {
      await devopsApi.restart();
    } catch {
      // 服务可能已开始关闭，忽略请求错误
    }
    await waitAndReload();
  };

  const onBuildRestart = async () => {
    if (busyRef.current || !window.confirm(t("buildRestartConfirm"))) return;
    busyRef.current = true;
    await beginBuild(() => devopsApi.buildAndRestart());
  };

  const onPull = async () => {
    if (busyRef.current || !window.confirm(t("pullConfirm"))) return;
    busyRef.current = true;
    setPhase("pulling");
    setNotice(null);
    try {
      const { data } = await devopsApi.update();
      setNotice(pullNotice(data));
    } catch (exc) {
      setNotice({ ok: false, text: String(exc) });
    }
    setPhase("idle");
    busyRef.current = false;
  };

  const onUpdateRestart = async () => {
    if (busyRef.current || !window.confirm(t("updateRestartConfirm"))) return;
    busyRef.current = true;
    await beginBuild(async () => {
      const resp = await devopsApi.updateAndRestart();
      if (resp.data?.ok && resp.data?.pull_result) {
        setNotice({ ok: true, text: resp.data.pull_result });
      }
      return resp;
    });
  };

  const pullNotice = (data: DevopsActionResult): Notice => {
    if (data.ok) {
      const upToDate = !data.pull_result || /Already up to date/i.test(data.pull_result);
      return { ok: true, text: upToDate ? t("pullUpToDate") : `${t("pullSuccess")}: ${data.pull_result}` };
    }
    if (data.error === "pull_conflict") return { ok: false, text: data.message || t("conflictHint") };
    if (data.error === "dirty_workspace") return { ok: false, text: data.message || t("dirtyHint") };
    return { ok: false, text: data.message || t("pullFailed") };
  };

  return (
    <div className="space-y-4 max-w-2xl">
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
        {buildState?.last && (
          <p className="mt-3 text-xs text-muted">
            {t("lastBuild")}: {buildState.last.finished_at} · {buildState.last.duration}s ·{" "}
            <span className={buildState.last.ok ? "text-ok" : "text-danger"}>
              {buildState.last.ok ? "OK" : t("buildFailed")}
            </span>
          </p>
        )}
      </Card>

      <Card title={t("projectUpdate")} subtitle={t("projectUpdateDesc")}>
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="secondary" size="sm" onClick={onPull} disabled={busy}>
            <GitBranch size={14} /> {t("pullCode")}
          </Button>
          <Button variant="primary" size="sm" onClick={onUpdateRestart} disabled={busy}>
            <Rocket size={14} /> {t("updateAndRestart")}
          </Button>
          {phase === "pulling" && <span className="text-xs text-muted">{t("pulling")}</span>}
          {notice && (
            <span className={`text-xs ${notice.ok ? "text-ok" : "text-danger"}`}>{notice.text}</span>
          )}
        </div>
      </Card>

      {phase === "failed" && errorLog && (
        <pre className="text-xs font-mono text-foreground bg-elevated border border-border rounded-md p-3 overflow-auto max-h-48 whitespace-pre-wrap">
          {errorLog}
        </pre>
      )}
    </div>
  );
}
