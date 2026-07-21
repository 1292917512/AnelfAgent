import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { approvalsApi } from "@/lib/api";
import { Save, RotateCcw, Plus, Trash2, AlertCircle } from "lucide-react";

interface ApprovalPolicy {
  tool_name_pattern: string;
  risk_level: string;
  requires_approval: boolean;
  timeout_seconds: number;
  on_timeout: string;
  trust_after_n_approvals: number;
  auto_approve_users: string[];
  auto_deny_users: string[];
  description: string;
}

export function ApprovalPolicyEditor() {
  const { t } = useTranslation("approvals");
  const queryClient = useQueryClient();
  const [policies, setPolicies] = useState<ApprovalPolicy[]>([]);
  const [dirty, setDirty] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["approvals", "policies"],
    queryFn: () => approvalsApi.policies().then((r) => r.data),
  });

  const saveMutation = useMutation({
    mutationFn: (policies: ApprovalPolicy[]) =>
      approvalsApi.savePolicies({ policies }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["approvals", "policies"] });
      setDirty(false);
    },
  });

  useEffect(() => {
    if (data?.policies) {
      setPolicies(data.policies);
    }
  }, [data]);

  const handleSave = () => {
    saveMutation.mutate(policies);
  };

  const handleReset = () => {
    if (data?.policies) {
      setPolicies(data.policies);
      setDirty(false);
    }
  };

  const handleAdd = () => {
    setPolicies([
      ...policies,
      {
        tool_name_pattern: "*",
        risk_level: "low",
        requires_approval: false,
        timeout_seconds: 60,
        on_timeout: "deny",
        trust_after_n_approvals: 0,
        auto_approve_users: [],
        auto_deny_users: [],
        description: "",
      },
    ]);
    setDirty(true);
  };

  const handleRemove = (index: number) => {
    setPolicies(policies.filter((_, i) => i !== index));
    setDirty(true);
  };

  const handleChange = (index: number, field: keyof ApprovalPolicy, value: unknown) => {
    const updated = [...policies];
    updated[index] = { ...updated[index], [field]: value } as ApprovalPolicy;
    setPolicies(updated);
    setDirty(true);
  };

  if (isLoading) {
    return <div className="text-center py-8 text-muted-foreground">{t("loading")}</div>;
  }

  return (
    <div className="space-y-4">
      {/* 操作栏 */}
      <div className="flex justify-between items-center p-4 bg-muted rounded-lg">
        <div className="flex gap-2">
          <button
            onClick={handleSave}
            disabled={!dirty || saveMutation.isPending}
            className="px-4 py-2 bg-primary text-primary-foreground rounded hover:bg-primary/90 disabled:opacity-50 flex items-center gap-2"
          >
            <Save className="w-4 h-4" />
            {t("save")}
          </button>
          <button
            onClick={handleReset}
            disabled={!dirty}
            className="px-4 py-2 bg-secondary text-secondary-foreground rounded hover:bg-secondary/90 disabled:opacity-50 flex items-center gap-2"
          >
            <RotateCcw className="w-4 h-4" />
            {t("reset")}
          </button>
        </div>
        <button
          onClick={handleAdd}
          className="px-4 py-2 bg-green-500 text-white rounded hover:bg-green-600 flex items-center gap-2"
        >
          <Plus className="w-4 h-4" />
          {t("addPolicy")}
        </button>
      </div>

      {/* 策略列表 */}
      <div className="space-y-4">
        {policies.map((policy, index) => (
          <div key={index} className="border border-border rounded-lg overflow-hidden">
            {/* 策略头部 */}
            <div className="flex items-center justify-between p-4 bg-muted">
              <div className="flex items-center gap-3">
                <div className="font-medium text-lg">{t("policy")} #{index + 1}</div>
                {policy.requires_approval && (
                  <div className="flex items-center gap-1 text-sm text-orange-600">
                    <AlertCircle className="w-4 h-4" />
                    <span>{t("requiresApproval")}</span>
                  </div>
                )}
              </div>
              <button
                onClick={() => handleRemove(index)}
                className="p-2 text-red-500 hover:bg-red-50 rounded transition-colors"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>

            {/* 策略内容 */}
            <div className="p-4 space-y-4">
              {/* 第一行：工具名称模式 + 风险等级 */}
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-foreground mb-2">
                    {t("toolNamePattern")}
                  </label>
                  <input
                    type="text"
                    value={policy.tool_name_pattern}
                    onChange={(e) => handleChange(index, "tool_name_pattern", e.target.value)}
                    className="w-full px-3 py-2 border border-border rounded focus:outline-none focus:ring-2 focus:ring-primary"
                    placeholder="filesystem.*"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-foreground mb-2">
                    {t("riskLevel")}
                  </label>
                  <select
                    value={policy.risk_level}
                    onChange={(e) => handleChange(index, "risk_level", e.target.value)}
                    className="w-full px-3 py-2 border border-border rounded focus:outline-none focus:ring-2 focus:ring-primary"
                  >
                    <option value="low">{t("risk.low")}</option>
                    <option value="medium">{t("risk.medium")}</option>
                    <option value="high">{t("risk.high")}</option>
                    <option value="critical">{t("risk.critical")}</option>
                  </select>
                </div>
              </div>

              {/* 第二行：需要批准 + 超时时间 + 超时处理 */}
              <div className="grid grid-cols-3 gap-4">
                <div className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    id={`requires-approval-${index}`}
                    checked={policy.requires_approval}
                    onChange={(e) => handleChange(index, "requires_approval", e.target.checked)}
                    className="w-4 h-4 rounded border-border focus:ring-2 focus:ring-primary"
                  />
                  <label
                    htmlFor={`requires-approval-${index}`}
                    className="text-sm font-medium text-foreground cursor-pointer"
                  >
                    {t("requiresApproval")}
                  </label>
                </div>

                <div>
                  <label className="block text-sm font-medium text-foreground mb-2">
                    {t("timeoutSeconds")}
                  </label>
                  <input
                    type="number"
                    value={policy.timeout_seconds}
                    onChange={(e) => handleChange(index, "timeout_seconds", Number(e.target.value))}
                    className="w-full px-3 py-2 border border-border rounded focus:outline-none focus:ring-2 focus:ring-primary"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-foreground mb-2">
                    {t("onTimeout")}
                  </label>
                  <select
                    value={policy.on_timeout}
                    onChange={(e) => handleChange(index, "on_timeout", e.target.value)}
                    className="w-full px-3 py-2 border border-border rounded focus:outline-none focus:ring-2 focus:ring-primary"
                  >
                    <option value="deny">{t("onTimeout.deny")}</option>
                    <option value="allow">{t("onTimeout.allow")}</option>
                    <option value="halt">{t("onTimeout.halt")}</option>
                  </select>
                </div>
              </div>

              {/* 第三行：信任阈值 */}
              <div>
                <label className="block text-sm font-medium text-foreground mb-2">
                  {t("trustAfterNApprovals")}
                </label>
                <input
                  type="number"
                  value={policy.trust_after_n_approvals}
                  onChange={(e) => handleChange(index, "trust_after_n_approvals", Number(e.target.value))}
                  className="w-full px-3 py-2 border border-border rounded focus:outline-none focus:ring-2 focus:ring-primary"
                  placeholder="0"
                />
                <p className="mt-1 text-xs text-muted-foreground">
                  {t("trustAfterNApprovalsHint")}
                </p>
              </div>

              {/* 第四行：描述 */}
              <div>
                <label className="block text-sm font-medium text-foreground mb-2">
                  {t("description")}
                </label>
                <input
                  type="text"
                  value={policy.description}
                  onChange={(e) => handleChange(index, "description", e.target.value)}
                  className="w-full px-3 py-2 border border-border rounded focus:outline-none focus:ring-2 focus:ring-primary"
                  placeholder={t("descriptionPlaceholder")}
                />
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
