/**
 * 统一权限规则编辑器 — 单一规则模型（allow/ask/deny + global/频道 scope）。
 *
 * 求值顺序说明见页面底部（与后端 PermissionRuleSet.evaluate 一致）。
 */
import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { approvalsApi } from "@/lib/api";
import type { PermissionRuleItem } from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Switch } from "@/components/ui/Switch";
import { LoadingBlock } from "@/components/ui/Spinner";
import { Save, RotateCcw, Plus, Trash2, ShieldCheck, ShieldX, ShieldQuestion } from "lucide-react";
import { cn } from "@/lib/utils";

/** 编辑器中的规则：id / created_by 可选（新规则无服务端字段），省略自动填充的字段 */
type EditableRule = Omit<PermissionRuleItem, "id" | "created_at" | "created_by"> & {
  id?: string;
  created_by?: string;
};

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
  const [rules, setRules] = useState<EditableRule[]>([]);
  const [defaultEffect, setDefaultEffect] = useState("allow");
  const [dirty, setDirty] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["approvals", "rules"],
    queryFn: () => approvalsApi.rules().then((r) => r.data),
  });

  const saveMutation = useMutation({
    mutationFn: () =>
      approvalsApi.saveRules({ rules, default_effect: defaultEffect }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["approvals", "rules"] });
      setDirty(false);
    },
  });

  useEffect(() => {
    if (data?.rules && !dirty) {
      setRules(data.rules);
      setDefaultEffect(data.default_effect ?? "allow");
    }
  }, [data, dirty]);

  const handleAdd = () => {
    setRules([
      ...rules,
      {
        pattern: "tool_name*",
        effect: "ask",
        scope: "global",
        users: [],
        risk_level: "medium",
        description: "",
        enabled: true,
      },
    ]);
    setDirty(true);
  };

  const handleChange = <K extends keyof EditableRule,>(index: number, field: K, value: EditableRule[K]) => {
    const updated = [...rules];
    const current = updated[index];
    if (!current) return;
    updated[index] = { ...current, [field]: value };
    setRules(updated);
    setDirty(true);
  };

  if (isLoading) {
    return <LoadingBlock label={t("loading")} />;
  }

  return (
    <div className="space-y-4">
      {data?.load_error && <div role="alert" className="rounded-lg border border-danger/30 bg-danger-subtle p-3 text-sm text-danger">{data.load_error}</div>}
      <div className="flex justify-between items-center gap-3 flex-wrap rounded-lg border border-border bg-card p-3">
        <div className="flex items-center gap-2 flex-wrap">
          <Button
            variant="primary"
            onClick={() => saveMutation.mutate()}
            disabled={!dirty || saveMutation.isPending}
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
            disabled={!dirty || saveMutation.isPending}
          >
            <RotateCcw size={14} />
            {t("reset")}
          </Button>
          <div className="flex items-center gap-2 text-sm ml-1">
            <span className="text-muted">{t("rules.defaultEffect")}</span>
            <Select
              disabled={saveMutation.isPending}
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
        <Button variant="primary" onClick={handleAdd} disabled={saveMutation.isPending}>
          <Plus size={14} />
          {t("rules.add")}
        </Button>
      </div>

      <fieldset disabled={saveMutation.isPending} className="min-w-0">
      <div className="space-y-3">
        {rules.map((rule, index) => {
          const EffectIcon = EFFECT_ICON[rule.effect] ?? ShieldQuestion;
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
                  className="flex-1 min-w-0 w-full sm:w-auto sm:min-w-[180px] font-mono"
                  placeholder="run_shell_command(npm test*)"
                  aria-label={t("toolNamePattern")}
                  title={t("rules.patternHint")}
                />
                <Select
                  aria-label={t("rules.effectLabel")}
                  value={rule.effect}
                  onChange={(e) => handleChange(index, "effect", e.target.value)}
                  className="w-28"
                >
                  <option value="allow">{t("rules.effect.allow")}</option>
                  <option value="ask">{t("rules.effect.ask")}</option>
                  <option value="deny">{t("rules.effect.deny")}</option>
                </Select>
                <Input
                  aria-label={t("rules.scope")}
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
                <Button
                  variant="ghost"
                  size="icon"
                  className="text-danger hover:bg-danger-subtle"
                  aria-label={t("rules.remove")}
                  onClick={() => {
                    setRules(rules.filter((_, i) => i !== index));
                    setDirty(true);
                  }}
                >
                  <Trash2 size={16} />
                </Button>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
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

      </fieldset>
      <div className="text-xs text-muted p-4 rounded-lg border border-border bg-elevated space-y-1">
        <div className="font-medium text-foreground">{t("rules.orderTitle")}</div>
        <div>{t("rules.orderDesc")}</div>
      </div>
    </div>
  );
}
