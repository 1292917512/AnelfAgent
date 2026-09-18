import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Trash2 } from "lucide-react";
import { faceApi } from "@/lib/api";
import { Badge, Button, Input, Modal, Spinner, toast } from "@/components/ui";
import { formatNs, onImgError } from "./format";

interface Props {
  personId: number;
  onClose: () => void;
}

/** 人物档案详情：基本信息编辑 + 样本池缩略图（可删）+ 近期出现事件。 */
export function PersonDetailModal({ personId, onClose }: Props) {
  const { t } = useTranslation("vision");
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["facePerson", personId],
    queryFn: () => faceApi.personDetail(personId).then((r) => r.data),
  });

  const [name, setName] = useState<string | null>(null);
  const [role, setRole] = useState<string | null>(null);
  const [notes, setNotes] = useState<string | null>(null);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["facePerson", personId] });
    queryClient.invalidateQueries({ queryKey: ["facePersons"] });
  };
  const onError = (err: unknown) => {
    const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
    toast.error(msg || t("face.messages.opFailed"));
  };

  const saveMut = useMutation({
    mutationFn: () => faceApi.updatePerson(personId, {
      name: name ?? undefined, role: role ?? undefined, notes: notes ?? undefined,
    }),
    onSuccess: () => { toast.success(t("face.messages.saveSuccess")); invalidate(); },
    onError,
  });

  const delSampleMut = useMutation({
    mutationFn: (sampleId: number) => faceApi.deleteSample(sampleId),
    onSuccess: () => { toast.success(t("face.messages.deleteSuccess")); invalidate(); },
    onError,
  });

  if (isLoading || !data) {
    return (
      <Modal open onClose={onClose} title={t("face.detail.title")}>
        <div className="flex justify-center py-8"><Spinner /></div>
      </Modal>
    );
  }

  const { person, samples, recent_events: recentEvents, effective_threshold: effThreshold } = data;

  return (
    <Modal open onClose={onClose} width="max-w-2xl"
      title={`${person.name || person.person_key}`}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>{t("common:close")}</Button>
          <Button loading={saveMut.isPending} onClick={() => saveMut.mutate()}>
            {t("common:save")}
          </Button>
        </>
      }>
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <Badge variant={person.status === "confirmed" ? "ok" : "warn"}>
            {t(`face.status.${person.status}`)}
          </Badge>
          <span className="font-mono text-muted">{person.person_key}</span>
          <span className="text-muted">
            {t("face.fields.matches")}: {person.match_count}
          </span>
          <span className="text-muted">
            {t("face.detail.threshold")}: {effThreshold}
            {person.threshold != null ? ` (${t("face.detail.custom")})` : ""}
          </span>
          {person.entity_scope && (
            <span className="font-mono text-accent">{person.entity_scope}</span>
          )}
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <label className="space-y-1 text-xs text-muted">
            <span>{t("face.fields.name")}</span>
            <Input value={name ?? person.name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="space-y-1 text-xs text-muted">
            <span>{t("face.fields.role")}</span>
            <Input value={role ?? person.role} onChange={(e) => setRole(e.target.value)} />
          </label>
        </div>
        <label className="space-y-1 text-xs text-muted block">
          <span>{t("face.fields.notes")}</span>
          <Input value={notes ?? person.notes} onChange={(e) => setNotes(e.target.value)} />
        </label>

        {/* 样本池 */}
        <div className="space-y-2">
          <div className="text-xs font-semibold text-heading">
            {t("face.detail.samples", { count: samples.length })}
          </div>
          {samples.length === 0
            ? <p className="text-xs text-muted">{t("face.detail.noSamples")}</p>
            : (
              <div className="grid grid-cols-3 md:grid-cols-5 gap-2">
                {samples.map((s) => (
                  <div key={s.id} className="relative rounded-md overflow-hidden border border-border group">
                    {s.image_path
                      ? <img src={faceApi.imageUrl(s.image_path)} alt=""
                          onError={onImgError}
                          className="w-full h-20 object-cover" loading="lazy" />
                      : <div className="w-full h-20 bg-elevated" />}
                    <div className="absolute inset-x-0 bottom-0 bg-black/60 text-white text-[10px] px-1 py-0.5 flex items-center justify-between">
                      <span>{(s.quality).toFixed(2)}</span>
                      <button
                        className="opacity-0 group-hover:opacity-100 transition-opacity"
                        onClick={() => delSampleMut.mutate(s.id)}
                        title={t("face.detail.deleteSample")}>
                        <Trash2 size={11} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
        </div>

        {/* 近期出现事件 */}
        <div className="space-y-2">
          <div className="text-xs font-semibold text-heading">{t("face.detail.recentEvents")}</div>
          {recentEvents.length === 0
            ? <p className="text-xs text-muted">{t("face.detail.noEvents")}</p>
            : (
              <div className="space-y-1">
                {recentEvents.map((ev) => (
                  <div key={ev.id} className="flex items-center gap-2 text-xs rounded-md bg-elevated px-2 py-1.5">
                    {ev.image_path && (
                      <img src={faceApi.imageUrl(ev.image_path)} alt=""
                        onError={onImgError}
                        className="w-10 h-10 rounded object-cover shrink-0" loading="lazy" />
                    )}
                    <span className="text-muted">{formatNs(ev.ts_ns)}</span>
                    <Badge variant="neutral">{ev.source || "-"}</Badge>
                    <span className="ml-auto text-muted">{ev.faces_count} 脸</span>
                  </div>
                ))}
              </div>
            )}
        </div>
      </div>
    </Modal>
  );
}
