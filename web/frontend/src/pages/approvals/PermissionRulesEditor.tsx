/**
 * 统一权限规则编辑器 — 单一规则模型（allow/ask/deny + global/频道 scope）。
 *
 * 替代旧 ApprovalPolicyEditor（三套白名单已合并进 users 限定规则）。
 * 求值顺序说明见页面底部（与后端 PermissionRuleSet.evaluate 一致）。
 */
import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { approvalsApi } from "@/lib/api";
import { Save, RotateCcw, Plus, Trash2, ShieldCheck, ShieldX, ShieldQuestion, Info } from "lucide-react";
import { cn } from "@/lib/utils";

interface PermissionRule {
  id?: string;
  pattern: string;
  effect: string;
  scope: string;
  users: string[];
  risk_level: string;
  timeout_seconds: number;
  on_timeout: string;
  description: string;
  enabled: boolean;
  created_by?: string;
}

const EFFECT_ICON: Record<string, typeof ShieldCheck> = {
  allow: ShieldCheck,
  ask: ShieldQuestion,
  deny: ShieldX,
};

const EFFECT_STYLE: Record<string, string> = {
  allow: "text-green-600",
  ask: "text-yellow-600",
  deny: "text-red-600",
};

export function PermissionRulesEditor() {
  const { t } = useTranslation("approvals");
  const queryClient = useQueryClient();
  const [rules, setRules] = useState<PermissionRule[]>([]);
  const [defaultEffect, setDefaultEffect] = useState("allow");
  const [sessionCount, setSessionCount] = useState(0);
  const [dirty, setDirty] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["approvals", "rules"],
    queryFn: () => approvalsApi.rules().then((r) => r.data),
  });

  const saveMutation = useMutation({
    mutationFn: () =>
      approvalsApi.saveRules({ rules: rules as unknown as Record<string, unknown>[], default_effect: defaultEffect }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["approvals", "rules"] });
      setDirty(false);
    },
  });

  useEffect(() => {
    if (data?.rules) {
      setRules(data.rules);
      setDefaultEffect(data.default_effect ?? "allow");
      setSessionCount(data.session_count ?? 0);
    }
  }, [data]);

  const handleAdd = () => {
    setRules([
      ...rules,
      {
        pattern: "tool_name*",
        effect: "ask",
        scope: "global",
        users: [],
        risk_level: "medium",
        timeout_seconds: 60,
        on_timeout: "deny",
        description: "",
        enabled: true,
      },
    ]);
    setDirty(true);
  };

  const handleChange = (index: number, field: keyof PermissionRule, value: unknown) => {
    const updated = [...rules];
    updated[index] = { ...updated[index], [field]: value } as PermissionRule;
    setRules(updated);
    setDirty(true);
  };

  if (isLoading) {
    return <div className="text-center py-8 text-muted-foreground">{t("loading")}</div>;
  }

  return (
    <div className="space-y-4">
      {/* 操作栏 */}
      <div className="flex justify-between items-center p-4 bg-muted rounded-lg">
        <div className="flex items-center gap-3">
          <button
            onClick={() => saveMutation.mutate()}
            disabled={!dirty || saveMutation.isPending}
            className="px-4 py-2 bg-primary text-primary-foreground rounded hover:bg-primary/90 disabled:opacity-50 flex items-center gap-2"
          >
            <Save className="w-4 h-4" />
            {t("save")}
          </button>
          <button
            onClick={() => {
              if (data?.rules) {
                setRules(data.rules);
                setDefaultEffect(data.default_effect ?? "allow");
                setDirty(false);
              }
            }}
            disabled={!dirty}
            className="px-4 py-2 bg-secondary text-secondary-foreground rounded hover:bg-secondary/90 disabled:opacity-50 flex items-center gap-2"
          >
            <RotateCcw className="w-4 h-4" />
            {t("reset")}
          </button>
          <div className="flex items-center gap-2 text-sm">
            <span className="text-muted-foreground">{t("rules.defaultEffect")}</span>
            <select
              value={defaultEffect}
              onChange={(e) => { setDefaultEffect(e.target.value); setDirty(true); }}
              className="px-2 py-1 border border-border rounded bg-background"
            >
              <option value="allow">{t("rules.effect.allow")}</option>
              <option value="ask">{t("rules.effect.ask")}</option>
              <option value="deny">{t("rules.effect.deny")}</option>
            </select>
          </div>
        </div>
        <button
          onClick={handleAdd}
          className="px-4 py-2 bg-green-500 text-white rounded hover:bg-green-600 flex items-center gap-2"
        >
          <Plus className="w-4 h-4" />
          {t("rules.add")}
        </button>
      </div>

      {sessionCount > 0 && (
        <div className="flex items-center gap-2 px-4 py-2 bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300 rounded text-sm">
          <Info className="w-4 h-4" />
          {t("rules.sessionNotice", { count: sessionCount })}
        </div>
      )}

      {/* 规则列表 */}
      <div className="space-y-3">
        {rules.map((rule, index) => {
          const EffectIcon = EFFECT_ICON[rule.effect] ?? ShieldQuestion;
          const isSession = rule.created_by?.startsWith("approve:");
          return (
            <div
              key={rule.id ?? index}
              className={cn("border border-border rounded-lg p-4 space-y-3", !rule.enabled && "opacity-50")}
            >
              <div className="flex items-center gap-3 flex-wrap">
                <EffectIcon className={cn("w-5 h-5 shrink-0", EFFECT_STYLE[rule.effect])} />
                <input
                  type="text"
                  value={rule.pattern}
                  onChange={(e) => handleChange(index, "pattern", e.target.value)}
                  className="flex-1 min-w-[220px] px-3 py-2 border border-border rounded font-mono text-sm focus:outline-none focus:ring-2 focus:ring-primary"
                  placeholder="run_shell_command(npm test*)"
                  title={t("rules.patternHint")}
                />
                <select
                  value={rule.effect}
                  onChange={(e) => handleChange(index, "effect", e.target.value)}
                  className="px-2 py-2 border border-border rounded bg-background text-sm"
                >
                  <option value="allow">{t("rules.effect.allow")}</option>
                  <option value="ask">{t("rules.effect.ask")}</option>
                  <option value="deny">{t("rules.effect.deny")}</option>
                </select>
                <input
                  type="text"
                  value={rule.scope}
                  onChange={(e) => handleChange(index, "scope", e.target.value)}
                  className="w-32 px-3 py-2 border border-border rounded text-sm focus:outline-none focus:ring-2 focus:ring-primary"
                  placeholder="global"
                  title={t("rules.scopeHint")}
                />
                <label className="flex items-center gap-1 text-sm text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={rule.enabled}
                    onChange={(e) => handleChange(index, "enabled", e.target.checked)}
                    className="w-4 h-4"
                  />
                  {t("rules.enabled")}
                </label>
                {isSession && (
                  <span className="text-xs px-2 py-0.5 rounded-full bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300">
                    {t("rules.sessionBadge")}
                  </span>
                )}
                <button
                  onClick={() => {
                    setRules(rules.filter((_, i) => i !== index));
                    setDirty(true);
                  }}
                  className="p-2 text-red-500 hover:bg-red-50 rounded transition-colors"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>

              <div className="grid grid-cols-4 gap-3">
                <div>
                  <label className="block text-xs text-muted-foreground mb-1">{t("rules.users")}</label>
                  <input
                    type="text"
                    value={rule.users.join(",")}
                    onChange={(e) =>
                      handleChange(index, "users", e.target.value.split(",").map((u) => u.trim()).filter(Boolean))
                    }
                    className="w-full px-2 py-1.5 border border-border rounded text-sm"
                    placeholder={t("rules.usersPlaceholder")}
                  />
                </div>
                <div>
                  <label className="block text-xs text-muted-foreground mb-1">{t("riskLevel")}</label>
                  <select
                    value={rule.risk_level}
                    onChange={(e) => handleChange(index, "risk_level", e.target.value)}
                    className="w-full px-2 py-1.5 border border-border rounded bg-background text-sm"
                  >
                    <option value="low">{t("risk.low")}</option>
                    <option value="medium">{t("risk.medium")}</option>
                    <option value="high">{t("risk.high")}</option>
                    <option value="critical">{t("risk.critical")}</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-muted-foreground mb-1">{t("timeoutSeconds")}</label>
                  <input
                    type="number"
                    value={rule.timeout_seconds}
                    onChange={(e) => handleChange(index, "timeout_seconds", Number(e.target.value))}
                    className="w-full px-2 py-1.5 border border-border rounded text-sm"
                  />
                </div>
                <div>
                  <label className="block text-xs text-muted-foreground mb-1">{t("onTimeout")}</label>
                  <select
                    value={rule.on_timeout}
                    onChange={(e) => handleChange(index, "on_timeout", e.target.value)}
                    className="w-full px-2 py-1.5 border border-border rounded bg-background text-sm"
                  >
                    <option value="deny">{t("onTimeout.deny")}</option>
                    <option value="allow">{t("onTimeout.allow")}</option>
                    <option value="halt">{t("onTimeout.halt")}</option>
                  </select>
                </div>
              </div>

              <input
                type="text"
                value={rule.description}
                onChange={(e) => handleChange(index, "description", e.target.value)}
                className="w-full px-3 py-2 border border-border rounded text-sm"
                placeholder={t("descriptionPlaceholder")}
              />
            </div>
          );
        })}
      </div>

      {/* 求值顺序说明 */}
      <div className="text-xs text-muted-foreground p-4 bg-muted rounded-lg space-y-1">
        <div className="font-medium">{t("rules.orderTitle")}</div>
        <div>{t("rules.orderDesc")}</div>
      </div>
    </div>
  );
}
