import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { audioApi, configMetaApi } from "@/lib/api";
import type { AudioStatus } from "@/lib/types";
import { Input, LoadingBlock, toast } from "@/components/ui";
import { Mic } from "lucide-react";

interface VoiceRow {
  key: string;
  label: string;
  unit: string;
  value: number;
}

const VOICE_UNITS: Record<string, string> = {
  voice_silence_ms: "ms",
  voice_min_utterance_ms: "ms",
  voice_max_utterance_s: "s",
  voice_vad_floor_min: "",
};

/** 语音会话：VAD/成段参数热编辑（agent/voice 核心语音系统的配置面） */
export function VoiceSessionPanel() {
  const { t } = useTranslation("sound");
  const queryClient = useQueryClient();
  const [rows, setRows] = useState<VoiceRow[]>([]);

  const { data: status, isLoading } = useQuery({
    queryKey: ["audioStatus"],
    queryFn: () => audioApi.status().then((r) => r.data as AudioStatus),
  });

  useEffect(() => {
    if (!status) return;
    setRows(
      Object.entries(status.voice_config ?? {}).map(([key, value]) => ({
        key,
        label: t(`voiceConfig.${key}`, { defaultValue: key }),
        unit: VOICE_UNITS[key] ?? "",
        value: Number(value),
      })),
    );
  }, [status, t]);

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
