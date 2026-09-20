/** 音色面板：预设库增删改查 + 默认/通话场景指派（AI 与 Web 共用同一音色库）。 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { audioApi } from "@/lib/api";
import type { VoicePresetEntry } from "@/lib/types";
import { Badge, Button, EmptyState, Input, LoadingBlock, Modal, Select, Textarea, toast } from "@/components/ui";
import { Card } from "@/components/common/Card";
import { AudioWaveform, Pencil, Plus, Trash2 } from "lucide-react";

const EMPTY_FORM = {
  id: "", name: "", voice_id: "", reference_audio: "", reference_text: "", note: "",
};
type PresetForm = typeof EMPTY_FORM;

function errorDetail(err: unknown): string {
  return String(
    (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? "",
  );
}

export function VoicePresetPanel() {
  const { t } = useTranslation("sound");
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<PresetForm | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["voicePresets"],
    queryFn: () => audioApi.voicePresets().then((r) => r.data),
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["voicePresets"] });
  };

  const saveMutation = useMutation({
    mutationFn: (form: PresetForm) => audioApi.saveVoicePreset(form),
    onSuccess: () => {
      refresh();
      setEditing(null);
      toast.success(t("voicePreset.saved"));
    },
    onError: (err: unknown) => {
      toast.error(errorDetail(err) || t("voicePreset.saveFailed"));
    },
  });
  const deleteMutation = useMutation({
    mutationFn: (id: string) => audioApi.deleteVoicePreset(id),
    onSuccess: () => {
      refresh();
      toast.success(t("voicePreset.deleted"));
    },
    onError: (err: unknown) => {
      toast.error(errorDetail(err) || t("voicePreset.deleteFailed"));
    },
  });
  const assignMutation = useMutation({
    mutationFn: (payload: { scene: "default" | "realtime"; presetId: string }) =>
      audioApi.assignVoice(payload.scene, payload.presetId),
    onSuccess: refresh,
    onError: (err: unknown) => {
      toast.error(errorDetail(err) || t("voicePreset.assignFailed"));
    },
  });

  const presets = data?.presets ?? [];
  const assignedDefault = data?.assignments.default ?? "";
  const assignedRealtime = data?.assignments.realtime ?? "";
  const isClone = (p: VoicePresetEntry) => Boolean(p.reference_audio);

  const submitForm = () => {
    if (!editing) return;
    if (!editing.name.trim()) {
      toast.error(t("voicePreset.nameRequired"));
      return;
    }
    saveMutation.mutate(editing);
  };

  return (
    <div className="space-y-6 max-w-2xl">
      <Card title={t("voicePreset.assignTitle")} subtitle={t("voicePreset.assignSubtitle")}>
        <div className="space-y-4">
          <div className="space-y-1">
            <label className="text-xs font-medium text-heading">
              {t("voicePreset.assignDefault")}
            </label>
            <Select
              className="w-full"
              value={assignedDefault}
              disabled={assignMutation.isPending}
              onChange={(e) =>
                assignMutation.mutate({ scene: "default", presetId: e.target.value })}
            >
              <option value="">{t("voicePreset.noneDefault")}</option>
              {presets.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </Select>
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-heading">
              {t("voicePreset.assignRealtime")}
            </label>
            <Select
              className="w-full"
              value={assignedRealtime}
              disabled={assignMutation.isPending}
              onChange={(e) =>
                assignMutation.mutate({ scene: "realtime", presetId: e.target.value })}
            >
              <option value="">{t("voicePreset.noneRealtime")}</option>
              {presets.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </Select>
          </div>
        </div>
      </Card>

      <Card
        title={t("voicePreset.libraryTitle")}
        subtitle={t("voicePreset.librarySubtitle")}
        actions={
          <Button size="sm" onClick={() => setEditing({ ...EMPTY_FORM })}>
            <Plus size={14} />
            {t("voicePreset.add")}
          </Button>
        }
      >
        {isLoading ? (
          <LoadingBlock />
        ) : presets.length === 0 ? (
          <EmptyState
            icon={AudioWaveform}
            title={t("voicePreset.emptyTitle")}
            description={t("voicePreset.emptyDesc")}
          />
        ) : (
          <div className="divide-y divide-border rounded-md border border-border">
            {presets.map((p) => (
              <div key={p.id} className="flex items-start gap-3 px-3 py-2.5">
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-sm font-medium text-heading">{p.name}</span>
                    <Badge variant={isClone(p) ? "warn" : "neutral"}>
                      {isClone(p) ? t("voicePreset.kindClone") : t("voicePreset.kindPreset")}
                    </Badge>
                    {assignedDefault === p.id && (
                      <Badge variant="ok">{t("voicePreset.badgeDefault")}</Badge>
                    )}
                    {assignedRealtime === p.id && (
                      <Badge variant="ok">{t("voicePreset.badgeRealtime")}</Badge>
                    )}
                  </div>
                  <div className="truncate font-mono text-[11px] text-muted">
                    {isClone(p)
                      ? `${t("voicePreset.referenceAudio")}: ${p.reference_audio}`
                      : `${t("voicePreset.voiceId")}: ${p.voice_id}`}
                  </div>
                  {p.note && <p className="text-[11px] text-muted">{p.note}</p>}
                  {isClone(p) && (
                    <p className="text-[10px] text-muted">{t("voicePreset.cloneNote")}</p>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  {assignedDefault !== p.id && (
                    <Button
                      size="sm" variant="ghost" disabled={assignMutation.isPending}
                      onClick={() => assignMutation.mutate({ scene: "default", presetId: p.id })}
                    >
                      {t("voicePreset.setDefault")}
                    </Button>
                  )}
                  {assignedRealtime !== p.id && !isClone(p) && (
                    <Button
                      size="sm" variant="ghost" disabled={assignMutation.isPending}
                      onClick={() => assignMutation.mutate({ scene: "realtime", presetId: p.id })}
                    >
                      {t("voicePreset.setRealtime")}
                    </Button>
                  )}
                  <Button
                    size="sm" variant="ghost"
                    onClick={() =>
                      setEditing({
                        id: p.id, name: p.name, voice_id: p.voice_id,
                        reference_audio: p.reference_audio,
                        reference_text: p.reference_text, note: p.note,
                      })}
                  >
                    <Pencil size={14} />
                  </Button>
                  <Button
                    size="sm" variant="ghost" disabled={deleteMutation.isPending}
                    onClick={() => deleteMutation.mutate(p.id)}
                  >
                    <Trash2 size={14} className="text-danger" />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Modal
        open={editing !== null}
        onClose={() => setEditing(null)}
        title={editing?.id ? t("voicePreset.edit") : t("voicePreset.add")}
        footer={
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setEditing(null)}>
              {t("common:cancel")}
            </Button>
            <Button onClick={submitForm} disabled={saveMutation.isPending}>
              {t("common:save")}
            </Button>
          </div>
        }
      >
        {editing && (
          <div className="space-y-3">
            <div className="space-y-1">
              <label className="text-xs font-medium text-heading">
                {t("voicePreset.name")} *
              </label>
              <Input
                value={editing.name}
                placeholder={t("voicePreset.namePlaceholder")}
                onChange={(e) => setEditing({ ...editing, name: e.target.value })}
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-heading">
                {t("voicePreset.voiceId")}
              </label>
              <Input
                value={editing.voice_id}
                placeholder={t("voicePreset.voiceIdPlaceholder")}
                onChange={(e) => setEditing({ ...editing, voice_id: e.target.value })}
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-heading">
                {t("voicePreset.referenceAudio")}
              </label>
              <Input
                value={editing.reference_audio}
                placeholder={t("voicePreset.referenceAudioPlaceholder")}
                onChange={(e) => setEditing({ ...editing, reference_audio: e.target.value })}
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-heading">
                {t("voicePreset.referenceText")}
              </label>
              <Input
                value={editing.reference_text}
                placeholder={t("voicePreset.referenceTextPlaceholder")}
                onChange={(e) => setEditing({ ...editing, reference_text: e.target.value })}
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-heading">{t("voicePreset.note")}</label>
              <Textarea
                rows={2}
                value={editing.note}
                placeholder={t("voicePreset.notePlaceholder")}
                onChange={(e) => setEditing({ ...editing, note: e.target.value })}
              />
            </div>
            <p className="text-[11px] text-muted">{t("voicePreset.eitherHint")}</p>
          </div>
        )}
      </Modal>
    </div>
  );
}
