import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Save } from "lucide-react";
import { voiceprintApi } from "./api";
import { Button, Input, Spinner, Switch, toast } from "@/components/ui";

/** 音源库设置：读写实体配置（阈值/样本池/FunASR/目录同步/令牌/webhook 等）。 */
export function SettingsPanel() {
  const { t } = useTranslation("voiceprint");
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [dirty, setDirty] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["voiceprintConfig"],
    queryFn: () => voiceprintApi.config().then((r) => r.data),
  });

  useEffect(() => {
    if (!data) return;
    const initial: Record<string, unknown> = {};
    for (const item of data.items) {
      initial[item.key] = item.current_value ?? item.default_value;
    }
    setDraft(initial);
    setDirty(false);
  }, [data]);

  const saveMutation = useMutation({
    mutationFn: () => voiceprintApi.updateConfig(draft),
    onSuccess: () => {
      setDirty(false);
      toast.success(t("messages.configSaved"));
      queryClient.invalidateQueries({ queryKey: ["voiceprintConfig"] });
      queryClient.invalidateQueries({ queryKey: ["voiceprintStats"] });
    },
    onError: () => toast.error(t("messages.opFailed")),
  });

  if (isLoading || !data) {
    return <div className="flex justify-center py-10"><Spinner /></div>;
  }

  const setValue = (key: string, value: unknown) => {
    setDraft((d) => ({ ...d, [key]: value }));
    setDirty(true);
  };

  return (
    <div className="space-y-4 max-w-2xl">
      {data.items.map((item) => (
        <div key={item.key} className="flex items-center gap-3">
          <div className="flex-1 min-w-0">
            <label className="text-xs font-medium text-foreground block">
              {item.key.replace(/^voiceprint_/, "")}
            </label>
            <p className="text-[10px] text-muted">{item.description}</p>
          </div>
          <div className="w-56 flex justify-end">
            {item.value_type === "boolean" ? (
              <Switch
                checked={Boolean(draft[item.key])}
                onChange={(v) => setValue(item.key, v)}
              />
            ) : (
              <Input
                type={
                  item.value_type === "integer" || item.value_type === "float"
                    ? "number" : "text"
                }
                step={item.value_type === "float" ? "0.01" : undefined}
                value={String(draft[item.key] ?? "")}
                onChange={(e) =>
                  setValue(
                    item.key,
                    item.value_type === "integer"
                      ? Number(e.target.value)
                      : item.value_type === "float"
                        ? e.target.value === "" ? "" : Number(e.target.value)
                        : e.target.value,
                  )
                }
              />
            )}
          </div>
        </div>
      ))}
      {dirty && (
        <Button size="sm" loading={saveMutation.isPending} onClick={() => saveMutation.mutate()}>
          <Save size={13} className="mr-1" />
          {t("common:save")}
        </Button>
      )}
    </div>
  );
}
