import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { audioApi, configMetaApi, type AudioStatus } from "@/lib/api";
import { Input, LoadingBlock, toast } from "@/components/ui";
import { Mic } from "lucide-react";

interface VoiceRow {
  key: string;
  label: string;
  unit: string;
  value: number;
}

const VOICE_LABELS: Record<string, { zh: string; en: string; unit: string }> = {
  voice_silence_ms: { zh: "静音判段阈值（毫秒）", en: "Silence endpoint threshold (ms)", unit: "ms" },
  voice_min_utterance_ms: { zh: "有效语音最短时长（毫秒）", en: "Min utterance length (ms)", unit: "ms" },
  voice_max_utterance_s: { zh: "单段语音最长时长（秒）", en: "Max utterance length (s)", unit: "s" },
  voice_vad_floor_min: { zh: "端点检测噪声地板下限（RMS）", en: "VAD noise floor min (RMS)", unit: "" },
};

/** 语音会话：VAD/成段参数热编辑（agent/voice 核心语音系统的配置面） */
export function VoiceSessionPanel() {
  const { t, i18n } = useTranslation("sound");
  const queryClient = useQueryClient();
  const [rows, setRows] = useState<VoiceRow[]>([]);

  const { data: status, isLoading } = useQuery({
    queryKey: ["audioStatus"],
    queryFn: () => audioApi.status().then((r) => r.data as AudioStatus),
  });

  useEffect(() => {
    if (!status) return;
    const lang = i18n.language.startsWith("zh") ? "zh" : "en";
    setRows(
      Object.entries(status.voice_config ?? {}).map(([key, value]) => ({
        key,
        label: VOICE_LABELS[key]?.[lang] ?? key,
        unit: VOICE_LABELS[key]?.unit ?? "",
        value: Number(value),
      })),
    );
  }, [status, i18n.language]);

  const saveMut = useMutation({
    mutationFn: ({ key, value }: { key: string; value: number }) => configMetaApi.save(key, value),
    onSuccess: () => {
      toast.success(t("saved"));
      queryClient.invalidateQueries({ queryKey: ["audioStatus"] });
    },
    onError: () => toast.error(t("saveFailed")),
  });

  if (isLoading) return <LoadingBlock label={t("common:loading")} />;

  return (
    <div className="space-y-3 max-w-2xl">
      <div className="rounded-md border border-border bg-card p-4 space-y-3">
        <div className="flex items-center gap-2">
          <Mic size={15} className="text-accent" />
          <span className="text-sm font-semibold text-heading">{t("voiceTitle")}</span>
        </div>
        <p className="text-xs text-muted">{t("voiceHint")}</p>
        {rows.map((row) => (
          <div key={row.key} className="flex items-center gap-3">
            <span className="flex-1 text-xs text-muted">{row.label}</span>
            <Input
              type="number"
              className="w-28"
              value={row.value}
              onChange={(e) => {
                const v = Number(e.target.value);
                setRows((rs) => rs.map((r) => (r.key === row.key ? { ...r, value: v } : r)));
              }}
              onBlur={() => saveMut.mutate({ key: row.key, value: row.value })}
            />
            {row.unit && <span className="text-[11px] text-muted w-8">{row.unit}</span>}
          </div>
        ))}
      </div>
    </div>
  );
}
