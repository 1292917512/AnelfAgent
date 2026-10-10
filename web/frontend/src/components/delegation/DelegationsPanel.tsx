import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Bot, Loader2 } from "lucide-react";
import { apiErrorMessage, delegationApi } from "@/lib/api";
import { useNow } from "@/hooks/useNow";
import { Card } from "@/components/common/Card";
import { QueryError } from "@/components/common/AsyncState";
import { RunningRow } from "./DelegationRows";
import { DelegationHistory } from "./DelegationHistory";
import { Button, Modal, Select, Textarea, toast } from "@/components/ui";
import type { DelegationHistoryItem, DelegationOverviewItem } from "@/lib/types";

const ProgressDrawer = lazy(() => import("./ProgressDrawer").then((module) => ({ default: module.ProgressDrawer })));

export function DelegationsPanel() {
  const { t } = useTranslation("plan");
  const queryClient = useQueryClient();
  const [progressTarget, setProgressTarget] = useState<DelegationOverviewItem | DelegationHistoryItem | null>(null);
  const [steerTarget, setSteerTarget] = useState<DelegationOverviewItem | null>(null);
  const [steerMessage, setSteerMessage] = useState("");
  const [steerMode, setSteerMode] = useState<"steer" | "after">("steer");
  const [cancellingIds, setCancellingIds] = useState<Set<string>>(new Set());

  const { data: overview, dataUpdatedAt, error: overviewError, isPending: overviewPending, refetch: refetchOverview } = useQuery({
    queryKey: ["delegations", "overview"],
    queryFn: () => delegationApi.overview().then((r) => r.data),
    refetchInterval: 3000,
  });
  const { data: history, error: historyError, isPending: historyPending, refetch: refetchHistory } = useQuery({
    queryKey: ["delegations", "history"],
    queryFn: () => delegationApi.history().then((r) => r.data),
    refetchInterval: 10000,
  });

  const running = useMemo(() => overview?.running ?? [], [overview]);
  const historyItems = (history?.items ?? []).filter((item) => !running.some((active) => active.delegation_id === item.delegation_id));
  const selectedProgress = progressTarget && (running.find((item) => item.delegation_id === progressTarget.delegation_id)
    ?? historyItems.find((item) => item.delegation_id === progressTarget.delegation_id) ?? progressTarget);

  // 运行中时每秒钟刷新耗时显示（数据本身 3s 轮询，漂移量本地补齐）
  const now = useNow(running.length > 0);
  const extraSeconds = running.length > 0 ? Math.max(0, (now - dataUpdatedAt) / 1000) : 0;

  // 已停止的委托从"取消中"集合清理
  useEffect(() => {
    setCancellingIds((prev) => {
      if (prev.size === 0) return prev;
      const alive = new Set(running.map((r) => r.delegation_id));
      const next = new Set([...prev].filter((id) => alive.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [running]);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["delegations"] });

  const cancelMutation = useMutation({
    mutationFn: (delegationId: string) => delegationApi.cancel(delegationId),
    onSuccess: (r, delegationId) => {
      if (r.data.status !== "ok") {
        setCancellingIds((prev) => {
          const next = new Set(prev);
          next.delete(delegationId);
          return next;
        });
        toast.error(r.data.error || t("delegation.panel.opFailed"));
        return;
      }
      invalidate();
    },
    onError: (err, delegationId) => {
      setCancellingIds((prev) => {
        const next = new Set(prev);
        next.delete(delegationId);
        return next;
      });
      toast.error(apiErrorMessage(err, t("delegation.panel.opFailed")));
    },
  });

  const steerMutation = useMutation({
    mutationFn: () => {
      if (!steerTarget) throw new Error(t("delegation.panel.opFailed"));
      return delegationApi.steer(steerTarget.delegation_id, steerMessage.trim(), steerMode);
    },
    onSuccess: (r) => {
      if (r.data.status !== "ok") {
        toast.error(r.data.error || t("delegation.panel.opFailed"));
        return;
      }
      toast.success(t("delegation.panel.steerSent"));
      setSteerTarget(null);
      setSteerMessage("");
      setSteerMode("steer");
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("delegation.panel.opFailed"))),
  });

  const handleCancel = (delegationId: string) => {
    setCancellingIds((prev) => new Set(prev).add(delegationId));
    cancelMutation.mutate(delegationId);
  };

  return (
    <Card
      title={t("delegation.panel.title")}
      subtitle={t("dashboard:overview.delegationHint")}
      className="delegation-panel"
      actions={
        running.length > 0 ? (
          <span className="inline-flex items-center gap-1.5 text-xs text-accent">
            <Loader2 size={12} className="animate-spin" />
            {t("activity.runningCount", { n: running.length })}
          </span>
        ) : undefined
      }
    >
      {overviewError && <QueryError compact error={overviewError} retry={() => void refetchOverview()} />}
      {historyError && <QueryError compact error={historyError} retry={() => void refetchHistory()} />}
      <div className="delegation-running">
        {running.length > 0 ? running.map((item) => <RunningRow
          key={item.delegation_id} item={item} extraSeconds={extraSeconds}
          cancelling={cancellingIds.has(item.delegation_id)}
          onShowProgress={() => setProgressTarget(item)}
          onSteer={() => setSteerTarget(item)}
          onCancel={() => handleCancel(item.delegation_id)}
        />) : <div className="delegation-idle"><Bot size={19} /><span>{overviewPending ? t("common:loading") : t("delegation.panel.empty")}</span></div>}
      </div>
      <DelegationHistory items={historyItems} pending={historyPending} onSelect={setProgressTarget} />

      {selectedProgress && (
        <Suspense fallback={<Modal open onClose={() => setProgressTarget(null)} title={t("dashboard:history.executionDetail")}><p role="status" className="text-sm text-muted">{t("common:loading")}</p></Modal>}><ProgressDrawer
          delegationId={selectedProgress.delegation_id}
          title={selectedProgress.goal || t("delegation.untitled")}
          item={selectedProgress}
          onClose={() => setProgressTarget(null)}
        /></Suspense>
      )}

      <Modal
        open={steerTarget !== null}
        onClose={() => setSteerTarget(null)}
        title={t("delegation.panel.steerTitle")}
        footer={
          <>
            <Button variant="ghost" onClick={() => setSteerTarget(null)}>
              {t("panel.cancel")}
            </Button>
            <Button
              variant="primary"
              loading={steerMutation.isPending}
              disabled={!steerMessage.trim()}
              onClick={() => steerMutation.mutate()}
            >
              {t("delegation.panel.steerSubmit")}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <p className="text-xs text-muted truncate">{steerTarget?.goal}</p>
          <Select value={steerMode} onChange={(e) => setSteerMode(e.target.value === "after" ? "after" : "steer")} className="w-full">
            <option value="steer">{t("delegation.panel.steerModeSteer")}</option>
            <option value="after">{t("delegation.panel.steerModeAfter")}</option>
          </Select>
          <Textarea
            value={steerMessage}
            onChange={(e) => setSteerMessage(e.target.value)}
            placeholder={t("delegation.panel.steerPlaceholder")}
            rows={4}
          />
        </div>
      </Modal>
    </Card>
  );
}
