import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { approvalsApi } from "@/lib/api";
import { CheckCircle, XCircle, Clock, AlertTriangle, ChevronDown, ChevronUp } from "lucide-react";

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

const riskLevelColor = (level: string): string => {
  switch (level.toLowerCase()) {
    case "low": return "text-blue-600 bg-blue-50";
    case "medium": return "text-yellow-600 bg-yellow-50";
    case "high": return "text-orange-600 bg-orange-50";
    case "critical": return "text-red-600 bg-red-50";
    default: return "text-gray-600 bg-gray-50";
  }
};

const riskLevelIcon = (level: string) => {
  switch (level.toLowerCase()) {
    case "critical": return <AlertTriangle className="w-5 h-5 text-red-500" />;
    case "high": return <AlertTriangle className="w-5 h-5 text-orange-500" />;
    case "medium": return <AlertTriangle className="w-5 h-5 text-yellow-500" />;
    default: return <Clock className="w-5 h-5 text-blue-500" />;
  }
};

export function PendingApprovalsList() {
  const { t } = useTranslation("approvals");
  const queryClient = useQueryClient();
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["approvals", "pending"],
    queryFn: () => approvalsApi.pending().then((r) => r.data),
    refetchInterval: 2000,
  });

  const approveMutation = useMutation({
    mutationFn: (requestId: string) => approvalsApi.approve(requestId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["approvals"] });
    },
  });

  const denyMutation = useMutation({
    mutationFn: (requestId: string) => approvalsApi.deny(requestId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["approvals"] });
    },
  });

  const pending = data?.pending || [];

  if (isLoading) {
    return <div className="text-center py-8 text-muted-foreground">{t("loading")}</div>;
  }

  if (pending.length === 0) {
    return (
      <div className="text-center py-12">
        <CheckCircle className="w-16 h-16 mx-auto text-green-500 mb-4" />
        <p className="text-lg text-muted-foreground">{t("noPendingApprovals")}</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {pending.map((req: ApprovalRequest) => {
        const expiresIn = Math.max(0, Math.floor(req.expires_at - Date.now() / 1000));
        const isExpiringSoon = expiresIn < 10;
        const isExpanded = expandedId === req.request_id;

        return (
          <div
            key={req.request_id}
            className="border border-border rounded-lg overflow-hidden hover:shadow-md transition-shadow"
          >
            {/* 头部 */}
            <div
              className="flex items-center justify-between p-4 bg-muted cursor-pointer"
              onClick={() => setExpandedId(isExpanded ? null : req.request_id)}
            >
              <div className="flex items-center gap-3 flex-1">
                {riskLevelIcon(req.risk_level)}
                <div className="flex-1">
                  <div className="font-mono text-sm font-medium">{req.tool_name}</div>
                  <div className="text-xs text-muted-foreground mt-1">
                    {t("requestedBy")}: {req.requester_user_id} ({req.requester_channel})
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-3">
                <div className={`px-3 py-1 rounded-full text-xs font-medium ${riskLevelColor(req.risk_level)}`}>
                  {t(`risk.${req.risk_level.toLowerCase()}`)}
                </div>
                <div className={`text-sm ${isExpiringSoon ? "text-red-500 font-bold" : "text-muted-foreground"}`}>
                  {isExpiringSoon && <AlertTriangle className="w-4 h-4 inline mr-1" />}
                  {expiresIn}s
                </div>
                {isExpanded ? <ChevronUp className="w-5 h-5" /> : <ChevronDown className="w-5 h-5" />}
              </div>
            </div>

            {/* 展开内容 */}
            {isExpanded && (
              <div className="p-4 space-y-4 border-t border-border">
                {/* 原因 */}
                <div>
                  <div className="text-sm font-medium text-foreground mb-2">{t("reason")}</div>
                  <div className="text-sm text-muted-foreground">{req.reason}</div>
                </div>

                {/* 参数 */}
                <div>
                  <div className="text-sm font-medium text-foreground mb-2">{t("arguments")}</div>
                  <pre className="text-xs bg-muted p-3 rounded overflow-x-auto">
                    {JSON.stringify(req.tool_args, null, 2)}
                  </pre>
                </div>

                {/* 操作按钮 */}
                <div className="flex gap-3 pt-2">
                  <button
                    onClick={() => approveMutation.mutate(req.request_id)}
                    disabled={approveMutation.isPending}
                    className="flex-1 px-4 py-2 bg-green-500 text-white rounded hover:bg-green-600 disabled:opacity-50 flex items-center justify-center gap-2"
                  >
                    <CheckCircle className="w-4 h-4" />
                    {t("approve")}
                  </button>
                  <button
                    onClick={() => denyMutation.mutate(req.request_id)}
                    disabled={denyMutation.isPending}
                    className="flex-1 px-4 py-2 bg-red-500 text-white rounded hover:bg-red-600 disabled:opacity-50 flex items-center justify-center gap-2"
                  >
                    <XCircle className="w-4 h-4" />
                    {t("deny")}
                  </button>
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
