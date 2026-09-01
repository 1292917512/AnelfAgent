/** 媒体库音色与默认参数面板。 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Save } from "lucide-react";
import { mediaApi } from "./api";
import { Card } from "@/components/common/Card";
import type { MediaConfig } from "./types";

const INPUT_CLS =
  "w-full px-2 py-1.5 rounded-md border border-border bg-elevated text-xs text-foreground font-mono";

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="text-xs font-medium text-foreground block mb-1">{label}</label>
      {children}
      {hint && <p className="text-[10px] text-muted mt-0.5">{hint}</p>}
    </div>
  );
}

export function DefaultsPanel() {
  const { t } = useTranslation("media");
  const queryClient = useQueryClient();
  const [form, setForm] = useState<Partial<MediaConfig>>({});
  const [dirty, setDirty] = useState(false);

  const { data: config } = useQuery({
    queryKey: ["media-config"],
    queryFn: () => mediaApi.config().then((r) => r.data),
  });

  useEffect(() => {
    if (config && !dirty) {
      setForm(config);
    }
  }, [config, dirty]);

  const setScalar = (key: keyof MediaConfig, value: string) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    setDirty(true);
  };
  const setDefault = (key: string, value: string | number) => {
    setForm((prev) => ({ ...prev, defaults: { ...(prev.defaults ?? {}), [key]: value } as MediaConfig["defaults"] }));
    setDirty(true);
  };

  const saveMutation = useMutation({
    mutationFn: () =>
      mediaApi.updateConfig({
        default_voice: form.default_voice ?? "",
        default_reference_audio: form.default_reference_audio ?? "",
        default_reference_text: form.default_reference_text ?? "",
        defaults: form.defaults,
      }),
    onSuccess: () => {
      setDirty(false);
      queryClient.invalidateQueries({ queryKey: ["media-config"] });
    },
  });

  const defaults = form.defaults ?? { image_size: "", video_resolution: "", video_duration: 0 };

  return (
    <div className="space-y-4 max-w-2xl">
      <Card title={t("defaults.voiceTitle")}>
        <div className="space-y-3">
          <Field label={t("defaults.defaultVoice")} hint={t("defaults.defaultVoiceHint")}>
            <input
              type="text"
              value={form.default_voice ?? ""}
              onChange={(e) => setScalar("default_voice", e.target.value)}
              placeholder="male-qn-qingse"
              className={INPUT_CLS}
            />
          </Field>
          <Field label={t("defaults.referenceAudio")} hint={t("defaults.referenceAudioHint")}>
            <input
              type="text"
              value={form.default_reference_audio ?? ""}
              onChange={(e) => setScalar("default_reference_audio", e.target.value)}
              placeholder="workspace/uploads/audio/xxx.mp3 或 https://..."
              className={INPUT_CLS}
            />
          </Field>
          <Field label={t("defaults.referenceText")}>
            <input
              type="text"
              value={form.default_reference_text ?? ""}
              onChange={(e) => setScalar("default_reference_text", e.target.value)}
              className={INPUT_CLS}
            />
          </Field>
        </div>
      </Card>

      <Card title={t("defaults.paramsTitle")}>
        <div className="space-y-3">
          <Field label={t("defaults.imageSize")} hint={t("defaults.imageSizeHint")}>
            <input
              type="text"
              value={defaults.image_size}
              onChange={(e) => setDefault("image_size", e.target.value)}
              placeholder="1024x1024"
              className={INPUT_CLS}
            />
          </Field>
          <Field label={t("defaults.videoResolution")} hint={t("defaults.videoResolutionHint")}>
            <input
              type="text"
              value={defaults.video_resolution}
              onChange={(e) => setDefault("video_resolution", e.target.value)}
              placeholder="768P / 1080P / 2K"
              className={INPUT_CLS}
            />
          </Field>
          <Field label={t("defaults.videoDuration")} hint={t("defaults.videoDurationHint")}>
            <input
              type="number"
              value={defaults.video_duration}
              onChange={(e) => setDefault("video_duration", Number(e.target.value) || 0)}
              className={INPUT_CLS}
            />
          </Field>
        </div>
      </Card>

      <button
        onClick={() => saveMutation.mutate()}
        disabled={!dirty || saveMutation.isPending}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-accent text-white text-xs font-medium hover:opacity-90 disabled:opacity-50"
      >
        <Save size={12} />
        {t("common.save")}
      </button>
    </div>
  );
}
