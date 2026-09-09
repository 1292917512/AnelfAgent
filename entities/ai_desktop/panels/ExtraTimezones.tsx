import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Globe2, Plus, X } from "lucide-react";
import { Button, Input } from "@/components/ui";
import { toast } from "@/stores/toast-store";
import type { DesktopModuleInfo } from "./types";

interface ExtraTimezonesProps {
  mod: DesktopModuleInfo;
  onSave: (key: string, value: unknown) => Promise<void>;
}

/**
 * 额外时区编辑器 — chip 列表管理（每个时区注入一行时间）。
 * 落盘为 extra_timezones 配置（逗号分隔），非法时区以警示色标记。
 */
export function ExtraTimezones({ mod, onSave }: ExtraTimezonesProps) {
  const { t } = useTranslation("ai_desktop");
  const [newZone, setNewZone] = useState("");
  const [busy, setBusy] = useState(false);

  const configItem = mod.configs.find((c) => c.name === "extra_timezones");
  const zones = mod.detail.extra_zones ?? [];
  if (!configItem) return null;

  const persist = async (names: string[]) => {
    setBusy(true);
    try {
      await onSave(configItem.key, names.join(","));
    } catch {
      toast.error(t("config.saveFailed"));
    } finally {
      setBusy(false);
    }
  };

  const addZone = () => {
    const name = newZone.trim();
    if (!name) return;
    if (zones.some((z) => z.timezone === name)) {
      toast.error(t("datetime.duplicate"));
      return;
    }
    void persist([...zones.map((z) => z.timezone), name]);
    setNewZone("");
  };

  const removeZone = (name: string) => {
    void persist(zones.filter((z) => z.timezone !== name).map((z) => z.timezone));
  };

  return (
    <div className="py-2">
      <div className="text-[13px] text-foreground mb-1.5">{t("datetime.extraTitle")}</div>
      {zones.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-2">
          {zones.map((z) => (
            <span
              key={z.timezone}
              className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] ${
                z.valid
                  ? "border-border bg-elevated text-foreground"
                  : "border-warn/40 bg-warn/10 text-warn"
              }`}
              title={z.valid ? undefined : t("datetime.invalidZone")}
            >
              <Globe2 className="h-3 w-3" />
              {z.timezone.split("/").pop()?.replace(/_/g, " ") ?? z.timezone}
              {z.valid && z.datetime && (
                <span className="text-muted font-mono">{z.datetime.slice(11, 16)}</span>
              )}
              <button
                type="button"
                disabled={busy || !mod.enabled}
                onClick={() => removeZone(z.timezone)}
                className="text-muted hover:text-danger transition-colors"
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="flex items-center gap-2">
        <Input
          className="h-8 flex-1 font-mono text-xs"
          value={newZone}
          disabled={busy || !mod.enabled}
          placeholder={t("datetime.addPlaceholder")}
          onChange={(e) => setNewZone(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") addZone();
          }}
        />
        <Button
          size="sm"
          disabled={busy || !mod.enabled || !newZone.trim()}
          onClick={addZone}
        >
          <Plus className="h-3.5 w-3.5" />
          {t("datetime.add")}
        </Button>
      </div>
      <p className="mt-1 text-[10px] text-muted">{t("datetime.zoneHint")}</p>
    </div>
  );
}
