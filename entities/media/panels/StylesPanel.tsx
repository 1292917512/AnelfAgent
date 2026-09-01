/** 媒体库风格预设面板：style_presets 的增删。 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Plus, Save, Trash2 } from "lucide-react";
import { mediaApi } from "./api";
import { Card } from "@/components/common/Card";

const INPUT_CLS =
  "w-full px-2 py-1.5 rounded-md border border-border bg-elevated text-xs text-foreground font-mono";

export function StylesPanel() {
  const { t } = useTranslation("media");
  const queryClient = useQueryClient();
  const [presets, setPresets] = useState<Record<string, string>>({});
  const [newName, setNewName] = useState("");
  const [newSuffix, setNewSuffix] = useState("");
  const [dirty, setDirty] = useState(false);

  const { data: config } = useQuery({
    queryKey: ["media-config"],
    queryFn: () => mediaApi.config().then((r) => r.data),
  });

  useEffect(() => {
    if (config && !dirty) {
      setPresets(config.style_presets ?? {});
    }
  }, [config, dirty]);

  const addPreset = () => {
    const name = newName.trim();
    if (!name || !newSuffix.trim() || presets[name] !== undefined) return;
    setPresets((prev) => ({ ...prev, [name]: newSuffix.trim() }));
    setNewName("");
    setNewSuffix("");
    setDirty(true);
  };

  const removePreset = (name: string) => {
    setPresets((prev) => {
      const next = { ...prev };
      delete next[name];
      return next;
    });
    setDirty(true);
  };

  const saveMutation = useMutation({
    mutationFn: () => mediaApi.updateConfig({ style_presets: presets }),
    onSuccess: () => {
      setDirty(false);
      queryClient.invalidateQueries({ queryKey: ["media-config"] });
    },
  });

  return (
    <div className="space-y-4 max-w-2xl">
      <p className="text-xs text-muted">{t("styles.hint")}</p>
      <Card title={t("styles.listTitle")}>
        <div className="space-y-2">
          {Object.keys(presets).length === 0 && (
            <p className="text-xs text-muted">{t("styles.empty")}</p>
          )}
          {Object.entries(presets).map(([name, suffix]) => (
            <div key={name} className="flex items-start justify-between gap-2 px-2.5 py-1.5 rounded-md border border-border bg-card">
              <div className="min-w-0">
                <div className="text-xs font-medium text-heading font-mono">{name}</div>
                <div className="text-[11px] text-muted break-all">{suffix}</div>
              </div>
              <button
                onClick={() => removePreset(name)}
                className="p-1 rounded text-muted hover:text-danger shrink-0"
              >
                <Trash2 size={13} />
              </button>
            </div>
          ))}
        </div>
      </Card>

      <Card title={t("styles.addTitle")}>
        <div className="space-y-2">
          <input
            type="text"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder={t("styles.namePlaceholder")}
            className={INPUT_CLS}
          />
          <textarea
            value={newSuffix}
            onChange={(e) => setNewSuffix(e.target.value)}
            placeholder={t("styles.suffixPlaceholder")}
            rows={3}
            className={INPUT_CLS}
          />
          <div className="flex items-center gap-2">
            <button
              onClick={addPreset}
              disabled={!newName.trim() || !newSuffix.trim() || presets[newName.trim()] !== undefined}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-secondary text-xs font-medium text-foreground hover:opacity-90 disabled:opacity-50"
            >
              <Plus size={12} />
              {t("styles.add")}
            </button>
            <button
              onClick={() => saveMutation.mutate()}
              disabled={!dirty || saveMutation.isPending}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-accent text-white text-xs font-medium hover:opacity-90 disabled:opacity-50"
            >
              <Save size={12} />
              {t("common.save")}
            </button>
          </div>
        </div>
      </Card>
    </div>
  );
}
