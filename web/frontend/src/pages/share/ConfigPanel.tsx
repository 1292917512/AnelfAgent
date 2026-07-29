import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { shareApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { Save } from "lucide-react";
import { toast } from "@/stores/toast-store";
import type { ShareConfig } from "@/lib/types";

const EXPIRES_OPTIONS = ["1h", "6h", "24h", "7d", "30d", "never"] as const;

export function ConfigPanel() {
  const { t } = useTranslation("share");
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<ShareConfig | null>(null);
  const [dirty, setDirty] = useState(false);

  const { data: config } = useQuery({
    queryKey: ["shareConfig"],
    queryFn: () => shareApi.getConfig().then((r) => r.data),
  });

  useEffect(() => {
    if (config) {
      setDraft(config);
      setDirty(false);
    }
  }, [config]);

  const saveMutation = useMutation({
    mutationFn: (data: ShareConfig) => shareApi.updateConfig(data),
    onSuccess: () => {
      toast.success(t("messages.saveSuccess"));
      setDirty(false);
      queryClient.invalidateQueries({ queryKey: ["shareConfig"] });
    },
    onError: () => toast.error(t("messages.saveFailed")),
  });

  const update = <K extends keyof ShareConfig>(key: K, value: ShareConfig[K]) => {
    setDraft((d) => (d ? { ...d, [key]: value } : d));
    setDirty(true);
  };

  if (!draft) {
    return (
      <div className="flex items-center justify-center py-12 text-sm text-muted">
        {t("common:loading")}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <Card
        title={t("tabs.config")}
        actions={
          dirty ? (
            <button
              onClick={() => saveMutation.mutate(draft)}
              disabled={saveMutation.isPending}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md bg-accent text-white hover:opacity-90 transition-all disabled:opacity-50"
            >
              <Save size={14} />
              {saveMutation.isPending ? t("common:loading") : t("config.save")}
            </button>
          ) : undefined
        }
      >
        <div className="space-y-5 max-w-lg">
          {/* 默认过期策略 */}
          <div>
            <label className="block text-sm font-medium text-heading mb-2">
              {t("config.defaultExpiresIn")}
            </label>
            <select
              value={draft.default_expires_in}
              onChange={(e) => update("default_expires_in", e.target.value)}
              className="w-full px-3 py-2 text-sm rounded-md border border-border bg-elevated text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
            >
              {EXPIRES_OPTIONS.map((opt) => (
                <option key={opt} value={opt}>
                  {t(`expires.${opt}`)}
                </option>
              ))}
            </select>
          </div>

          {/* Token 长度 */}
          <div>
            <label className="block text-sm font-medium text-heading mb-2">
              {t("config.tokenLength")}
            </label>
            <input
              type="number"
              min={8}
              max={64}
              value={draft.token_length}
              onChange={(e) => update("token_length", parseInt(e.target.value, 10) || 22)}
              className="w-full px-3 py-2 text-sm rounded-md border border-border bg-elevated text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
            />
            <p className="text-xs text-muted mt-1">8 - 64 字符，默认 22</p>
          </div>

          {/* 默认下载上限 */}
          <div>
            <label className="block text-sm font-medium text-heading mb-2">
              {t("config.defaultMaxDownloads")}
            </label>
            <input
              type="number"
              min={0}
              value={draft.default_max_downloads}
              onChange={(e) => update("default_max_downloads", parseInt(e.target.value, 10) || 0)}
              className="w-full px-3 py-2 text-sm rounded-md border border-border bg-elevated text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
            />
            <p className="text-xs text-muted mt-1">0 = {t("unlimited")}</p>
          </div>

          {/* AI 自动分享 */}
          <div className="flex items-center justify-between">
            <div>
              <label className="block text-sm font-medium text-heading">
                {t("config.aiAutoShare")}
              </label>
              <p className="text-xs text-muted mt-0.5">
                允许 AI 通过工具自动创建分享链接
              </p>
            </div>
            <button
              onClick={() => update("ai_auto_share", !draft.ai_auto_share)}
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                draft.ai_auto_share ? "bg-accent" : "bg-border"
              }`}
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                  draft.ai_auto_share ? "translate-x-6" : "translate-x-1"
                }`}
              />
            </button>
          </div>

          {/* 下载审计 */}
          <div className="flex items-center justify-between">
            <div>
              <label className="block text-sm font-medium text-heading">
                {t("config.auditEnabled")}
              </label>
              <p className="text-xs text-muted mt-0.5">
                记录每次下载的 IP / 时间 / User-Agent
              </p>
            </div>
            <button
              onClick={() => update("audit_enabled", !draft.audit_enabled)}
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                draft.audit_enabled ? "bg-accent" : "bg-border"
              }`}
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                  draft.audit_enabled ? "translate-x-6" : "translate-x-1"
                }`}
              />
            </button>
          </div>
        </div>
      </Card>
    </div>
  );
}
