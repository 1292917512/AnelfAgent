/** 声音生成能力面板：能力优先级链 + 默认音色/参考音频配置。 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { audioApi, configMetaApi } from "@/lib/api";
import { CapabilityChainPanel } from "@/components/common/CapabilityChainPanel";
import { Card } from "@/components/common/Card";

const CAPS = ["tts", "voice_mgmt", "music"];

const DEFAULT_FIELDS = [
  "tts_default_voice",
  "tts_default_reference_audio",
  "tts_default_reference_text",
] as const;

export function SoundGenerationPanel() {
  const { t } = useTranslation("sound");
  const queryClient = useQueryClient();

  const { data: status } = useQuery({
    queryKey: ["soundCapabilities"],
    queryFn: () => audioApi.capabilities().then((r) => r.data),
  });
  const { data: meta } = useQuery({
    queryKey: ["configMeta"],
    queryFn: () => configMetaApi.list().then((r) => r.data),
  });

  const valueOf = (key: string): string => {
    const item = meta?.groups.flatMap((g) => g.items).find((i) => i.key === key);
    return typeof item?.value === "string" ? item.value : "";
  };

  const saveMutation = useMutation({
    mutationFn: ({ key, value }: { key: string; value: string }) =>
      configMetaApi.save(key, value),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["configMeta"] }),
  });

  return (
    <div className="space-y-6 max-w-2xl">
      {status && (
        <CapabilityChainPanel
          status={status}
          caps={CAPS}
          configKey="sound_provider_priority"
          ns="sound"
          queryKey="soundCapabilities"
        />
      )}

      <Card title={t("generation.defaultsTitle")} subtitle={t("generation.defaultsSubtitle")}>
        <div className="space-y-3">
          {DEFAULT_FIELDS.map((key) => (
            <div key={key} className="space-y-1">
              <label className="text-xs font-medium text-heading">
                {t(`generation.fields.${key}`)}
              </label>
              <input
                type="text"
                key={`${key}:${valueOf(key)}`}
                defaultValue={valueOf(key)}
                placeholder={t(`generation.placeholders.${key}`)}
                onBlur={(e) => {
                  if (e.target.value !== valueOf(key)) {
                    saveMutation.mutate({ key, value: e.target.value.trim() });
                  }
                }}
                className="w-full px-2.5 py-1.5 rounded-md border border-border bg-elevated text-xs text-foreground font-mono"
              />
              <p className="text-[10px] text-muted">{t(`generation.descs.${key}`)}</p>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
