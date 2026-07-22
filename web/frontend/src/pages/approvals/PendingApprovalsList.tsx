import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { approvalsApi } from "@/lib/api";
import { Badge, type BadgeVariant } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { EmptyState } from "@/components/ui/EmptyState";
import { LoadingBlock } from "@/components/ui/Spinner";
import { cn } from "@/lib/utils";
import {
  ShieldCheck,
  ShieldAlert,
  Check,
  X,
  ChevronDown,
  ChevronUp,
} from "lucide-react";

interface ApprovalRequest {
  request_id: string;
  tool_name: string;
  tool_args: Record<string, unknown>;
  risk_level: string;
  reason: string;
  requester_channel: string;
  requester_chat_id: string;
  requester_user_id: string;
  expires_at: number;
  created_at: number;
}

type Remember = "once" | "session" | "always";

const RISK_VARIANT: Record<string, BadgeVariant> = {
  low: "info",
  medium: "warn",
  high: "danger",
  critical: "danger",
};

/** 每秒刷新一次的当前时间，用于倒计时 */
function useNow(enabled: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!enabled) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [enabled]);
  return now;
}

export function PendingApprovalsList() {
  const { t } = useTranslation("approvals");
  const queryClient = useQueryClient();
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [remember, setRemember] = useState<Remember>("once");
  const [feedback, setFeedback] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["approvals", "pending"],
    queryFn: () => approvalsApi.pending().then((r) => r.data),
    refetchInterval: 2000,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["approvals"] });

  const approveMutation = useMutation({
    mutationFn: ({ id, reason, remember }: { id: string; reason?: string; remember?: Remember }) =>
      approvalsApi.approve(id, reason, remember ?? "once"),
    onSuccess: invalidate,
  });

  const denyMutation = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason?: string }) => approvalsApi.deny(id, reason),
    onSuccess: invalidate,
  });

  const pending = (data?.pending ?? []) as ApprovalRequest[];
  const now = useNow(pending.length > 0);

  if (isLoading) {
    return <LoadingBlock label={t("loading")} />;
  }

  if (pending.length === 0) {
    return (
      <EmptyState
        icon={ShieldCheck}
        title={t("noPendingApprovals")}
        description={t("noPendingHint")}
      />
    );
  }

  const busy = approveMutation.isPending || denyMutation.isPending;

  return (
    <div className="space-y-3">
      {pending.map((req) => {
        const expiresIn = Math.max(0, Math.floor(req.expires_at - now / 1000));
        const total = Math.max(1, Math.floor(req.expires_at - req.created_at));
        const pct = Math.min(100, Math.max(0, (expiresIn / total) * 100));
        const isExpiringSoon = expiresIn < 10;
        const isExpanded = expandedId === req.request_id;
        const riskVariant = RISK_VARIANT[req.risk_level.toLowerCase()] ?? "neutral";

        return (
          <div
            key={req.request_id}
            className="rounded-lg border border-border bg-card overflow-hidden transition-shadow hover:shadow-md animate-rise"
          >
            {/* 头部 */}
            <div
              className="flex items-center gap-3 p-4 cursor-pointer"
              onClick={() => setExpandedId(isExpanded ? null : req.request_id)}
            >
              <ShieldAlert
                size={20}
                className={cn(
                  "shrink-0",
                  riskVariant === "danger" ? "text-danger" : riskVariant === "warn" ? "text-warn" : "text-info",
                )}
              />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-mono text-sm font-semibold text-heading truncate">{req.tool_name}</span>
                  <Badge variant={riskVariant}>{t(`risk.${req.risk_level.toLowerCase()}`)}</Badge>
                </div>
                <div className="text-xs text-muted mt-1 truncate">
                  {t("requestedBy")}: {req.requester_user_id} ({req.requester_channel})
                </div>
              </div>

              {/* 倒计时 */}
              <div className="hidden sm:flex flex-col items-end gap-1 w-24 shrink-0">
                <span className={cn("text-xs font-mono", isExpiringSoon ? "text-danger font-bold" : "text-muted")}>
                  {expiresIn}s
                </span>
                <div className="h-1 w-full rounded-full bg-secondary overflow-hidden">
                  <div
                    className={cn("h-full rounded-full transition-all duration-1000", isExpiringSoon ? "bg-danger" : "bg-accent")}
                    style={{ width: `${pct}%` }}
                  />
                </div>
              </div>

              {/* 快捷操作 */}
              <div className="flex items-center gap-1.5 shrink-0" onClick={(e) => e.stopPropagation()}>
                <Button
                  size="sm"
                  variant="primary"
                  disabled={busy}
                  onClick={() => approveMutation.mutate({ id: req.request_id })}
                >
                  <Check size={14} />
                  {t("approve")}
                </Button>
                <Button
                  size="sm"
                  variant="danger"
                  disabled={busy}
                  onClick={() => denyMutation.mutate({ id: req.request_id })}
                >
                  <X size={14} />
                  {t("deny")}
                </Button>
              </div>

              {isExpanded ? <ChevronUp size={16} className="shrink-0 text-muted" /> : <ChevronDown size={16} className="shrink-0 text-muted" />}
            </div>

            {/* 展开内容 */}
            {isExpanded && (
              <div className="px-4 pb-4 pt-3 space-y-4 border-t border-border">
                {req.reason && (
                  <div>
                    <div className="text-xs font-medium text-muted mb-1">{t("reason")}</div>
                    <div className="text-sm text-foreground">{req.reason}</div>
                  </div>
                )}

                <div>
                  <div className="text-xs font-medium text-muted mb-1">{t("arguments")}</div>
                  <pre className="text-xs bg-elevated border border-border p-3 rounded-md overflow-x-auto font-mono text-foreground">
                    {JSON.stringify(req.tool_args, null, 2)}
                  </pre>
                </div>

                {/* 决策选项 */}
                <div className="flex flex-col sm:flex-row gap-2">
                  <Input
                    value={feedback}
                    onChange={(e) => setFeedback(e.target.value)}
                    placeholder={t("popup.feedbackPlaceholder")}
                    className="flex-1"
                  />
                  <Select value={remember} onChange={(e) => setRemember(e.target.value as Remember)}>
                    <option value="once">{t("popup.allowOnce")}</option>
                    <option value="session">{t("popup.allowSession")}</option>
                    <option value="always">{t("popup.allowAlways")}</option>
                  </Select>
                </div>

                <div className="flex gap-2">
                  <Button
                    variant="primary"
                    className="flex-1"
                    loading={approveMutation.isPending}
                    disabled={busy}
                    onClick={() =>
                      approveMutation.mutate({ id: req.request_id, reason: feedback || undefined, remember })
                    }
                  >
                    <Check size={14} />
                    {t("approve")}
                  </Button>
                  <Button
                    variant="danger"
                    className="flex-1"
                    loading={denyMutation.isPending}
                    disabled={busy}
                    onClick={() => denyMutation.mutate({ id: req.request_id, reason: feedback || undefined })}
                  >
                    <X size={14} />
                    {t("deny")}
                  </Button>
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
