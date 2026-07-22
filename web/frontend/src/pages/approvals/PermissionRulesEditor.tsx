/**
 * 统一权限规则编辑器 — 单一规则模型（allow/ask/deny + global/频道 scope）。
 *
 * 求值顺序说明见页面底部（与后端 PermissionRuleSet.evaluate 一致）。
 */
import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { approvalsApi } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Badge } from "@/components/ui/Badge";
import { Switch } from "@/components/ui/Switch";
import { LoadingBlock } from "@/components/ui/Spinner";
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
  allow: "text-ok",
  ask: "text-warn",
  deny: "text-danger",
};

const EFFECT_ACCENT: Record<string, string> = {
  allow: "border-l-ok",
  ask: "border-l-warn",
  deny: "border-l-danger",
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
    return <LoadingBlock label={t("loading")} />;
  }

  return (
    <div className="space-y-4">
      {/* 操作栏 */}
      <div className="flex justify-between items-center gap-3 flex-wrap rounded-lg border border-border bg-card p-3">
        <div className="flex items-center gap-2 flex-wrap">
          <Button
            variant="primary"
            onClick={() => saveMutation.mutate()}
            disabled={!dirty}
            loading={saveMutation.isPending}
          >
            <Save size={14} />
            {t("save")}
          </Button>
          <Button
            onClick={() => {
              if (data?.rules) {
                setRules(data.rules);
                setDefaultEffect(data.default_effect ?? "allow");
                setDirty(false);
              }
            }}
            disabled={!dirty}
          >
            <RotateCcw size={14} />
            {t("reset")}
          </Button>
          <div className="flex items-center gap-2 text-sm ml-1">
            <span className="text-muted">{t("rules.defaultEffect")}</span>
            <Select
              value={defaultEffect}
              onChange={(e) => { setDefaultEffect(e.target.value); setDirty(true); }}
              className="w-28"
            >
              <option value="allow">{t("rules.effect.allow")}</option>
              <option value="ask">{t("rules.effect.ask")}</option>
              <option value="deny">{t("rules.effect.deny")}</option>
            </Select>
          </div>
        </div>
        <Button variant="primary" onClick={handleAdd}>
          <Plus size={14} />
          {t("rules.add")}
        </Button>
      </div>

      {sessionCount > 0 && (
        <div className="flex items-center gap-2 px-4 py-2.5 rounded-md border border-info/30 bg-accent-subtle text-info text-sm">
          <Info size={16} className="shrink-0" />
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
              className={cn(
                "rounded-lg border border-border border-l-4 bg-card p-4 space-y-3 animate-rise",
                EFFECT_ACCENT[rule.effect] ?? "border-l-border",
                !rule.enabled && "opacity-50",
              )}
            >
              <div className="flex items-center gap-3 flex-wrap">
                <EffectIcon size={20} className={cn("shrink-0", EFFECT_STYLE[rule.effect])} />
                <Input
                  value={rule.pattern}
                  onChange={(e) => handleChange(index, "pattern", e.target.value)}
                  className="flex-1 min-w-[220px] font-mono"
                  placeholder="run_shell_command(npm test*)"
                  title={t("rules.patternHint")}
                />
                <Select
                  value={rule.effect}
                  onChange={(e) => handleChange(index, "effect", e.target.value)}
                  className="w-28"
                >
                  <option value="allow">{t("rules.effect.allow")}</option>
                  <option value="ask">{t("rules.effect.ask")}</option>
                  <option value="deny">{t("rules.effect.deny")}</option>
                </Select>
                <Input
                  value={rule.scope}
                  onChange={(e) => handleChange(index, "scope", e.target.value)}
                  className="w-32"
                  placeholder="global"
                  title={t("rules.scopeHint")}
                />
                <label className="flex items-center gap-2 text-sm text-muted">
                  <Switch
                    checked={rule.enabled}
                    onChange={(v) => handleChange(index, "enabled", v)}
                  />
                  {t("rules.enabled")}
                </label>
                {isSession && <Badge variant="info">{t("rules.sessionBadge")}</Badge>}
                <Button
                  variant="ghost"
                  size="icon"
                  className="text-danger hover:bg-danger-subtle"
                  onClick={() => {
                    setRules(rules.filter((_, i) => i !== index));
                    setDirty(true);
                  }}
                >
                  <Trash2 size={16} />
                </Button>
              </div>

              <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                <div>
                  <label className="block text-xs text-muted mb-1">{t("rules.users")}</label>
                  <Input
                    value={rule.users.join(",")}
                    onChange={(e) =>
                      handleChange(index, "users", e.target.value.split(",").map((u) => u.trim()).filter(Boolean))
                    }
                    placeholder={t("rules.usersPlaceholder")}
                  />
                </div>
                <div>
                  <label className="block text-xs text-muted mb-1">{t("riskLevel")}</label>
                  <Select
                    value={rule.risk_level}
                    onChange={(e) => handleChange(index, "risk_level", e.target.value)}
                    className="w-full"
                  >
                    <option value="low">{t("risk.low")}</option>
                    <option value="medium">{t("risk.medium")}</option>
                    <option value="high">{t("risk.high")}</option>
                    <option value="critical">{t("risk.critical")}</option>
                  </Select>
                </div>
                <div>
                  <label className="block text-xs text-muted mb-1">{t("timeoutSeconds")}</label>
                  <Input
                    type="number"
                    value={rule.timeout_seconds}
                    onChange={(e) => handleChange(index, "timeout_seconds", Number(e.target.value))}
                  />
                </div>
                <div>
                  <label className="block text-xs text-muted mb-1">{t("onTimeoutLabel")}</label>
                  <Select
                    value={rule.on_timeout}
                    onChange={(e) => handleChange(index, "on_timeout", e.target.value)}
                    className="w-full"
                  >
                    <option value="deny">{t("onTimeout.deny")}</option>
                    <option value="allow">{t("onTimeout.allow")}</option>
                    <option value="halt">{t("onTimeout.halt")}</option>
                  </Select>
                </div>
              </div>

              <Input
                value={rule.description}
                onChange={(e) => handleChange(index, "description", e.target.value)}
                placeholder={t("descriptionPlaceholder")}
              />
            </div>
          );
        })}
      </div>

      {/* 求值顺序说明 */}
      <div className="text-xs text-muted p-4 rounded-lg border border-border bg-elevated space-y-1">
        <div className="font-medium text-foreground">{t("rules.orderTitle")}</div>
        <div>{t("rules.orderDesc")}</div>
      </div>
    </div>
  );
}
