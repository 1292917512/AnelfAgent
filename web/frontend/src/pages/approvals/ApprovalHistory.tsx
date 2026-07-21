import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { approvalsApi } from "@/lib/api";
import { CheckCircle, XCircle, Clock, Ban, ChevronDown, ChevronUp } from "lucide-react";

interface ApprovalHistoryItem {
  request_id: string;
  tool_name: string;
  risk_level: string;
  decision: string;
  decided_by: string;
  decided_at: number;
  decision_reason: string;
  requester_user_id: string;
  requester_channel: string;
}

const decisionIcon = (decision: string) => {
  switch (decision) {
    case "approved": return <CheckCircle className="w-5 h-5 text-green-500" />;
    case "denied": return <XCircle className="w-5 h-5 text-red-500" />;
    case "expired": return <Clock className="w-5 h-5 text-yellow-500" />;
    case "cancelled": return <Ban className="w-5 h-5 text-gray-500" />;
    default: return null;
  }
};

const decisionColor = (decision: string): string => {
  switch (decision) {
    case "approved": return "text-green-600 bg-green-50";
    case "denied": return "text-red-600 bg-red-50";
    case "expired": return "text-yellow-600 bg-yellow-50";
    case "cancelled": return "text-gray-600 bg-gray-50";
    default: return "text-gray-600 bg-gray-50";
  }
};

export function ApprovalHistory() {
  const { t } = useTranslation("approvals");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["approvals", "history"],
    queryFn: () => approvalsApi.history(100).then((r) => r.data),
  });

  const history = data?.history || [];

  if (isLoading) {
    return <div className="text-center py-8 text-muted-foreground">{t("loading")}</div>;
  }

  if (history.length === 0) {
    return (
      <div className="text-center py-12 text-muted-foreground">
        {t("noHistory")}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {history.map((item: ApprovalHistoryItem) => {
        const isExpanded = expandedId === item.request_id;

        return (
          <div
            key={item.request_id}
            className="border border-border rounded-lg overflow-hidden hover:shadow-sm transition-shadow"
          >
            {/* 头部 */}
            <div
              className="flex items-center justify-between p-3 bg-muted cursor-pointer"
              onClick={() => setExpandedId(isExpanded ? null : item.request_id)}
            >
              <div className="flex items-center gap-3 flex-1">
                {decisionIcon(item.decision)}
                <div className="flex-1">
                  <div className="font-mono text-sm">{item.tool_name}</div>
                  <div className="text-xs text-muted-foreground mt-1">
                    {item.requester_user_id} ({item.requester_channel})
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-3">
                <div className={`px-3 py-1 rounded-full text-xs font-medium ${decisionColor(item.decision)}`}>
                  {t(`decision.${item.decision}`)}
                </div>
                <div className="text-xs text-muted-foreground">
                  {new Date(item.decided_at * 1000).toLocaleString()}
                </div>
                {isExpanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
              </div>
            </div>

            {/* 展开内容 */}
            {isExpanded && (
              <div className="p-4 space-y-3 border-t border-border">
                {/* 决策人 */}
                <div>
                  <div className="text-sm font-medium text-foreground mb-1">{t("decidedBy")}</div>
                  <div className="text-sm text-muted-foreground">{item.decided_by || t("system")}</div>
                </div>

                {/* 决策理由 */}
                {item.decision_reason && (
                  <div>
                    <div className="text-sm font-medium text-foreground mb-1">{t("decisionReason")}</div>
                    <div className="text-sm text-muted-foreground">{item.decision_reason}</div>
                  </div>
                )}

                {/* 风险等级 */}
                <div>
                  <div className="text-sm font-medium text-foreground mb-1">{t("riskLevel")}</div>
                  <div className="text-sm text-muted-foreground">{t(`risk.${item.risk_level.toLowerCase()}`)}</div>
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
