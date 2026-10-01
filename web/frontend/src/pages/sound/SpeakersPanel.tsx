import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Focus, GitMerge, Grid3X3, Pencil, Plus, RefreshCw, Trash2, UserCheck } from "lucide-react";
import { audioApi } from "@/lib/api";
import type { SpeakerListItem } from "./types";
import { Card } from "@/components/common/Card";
import {
  Badge, Button, ConfirmDialog, EmptyState, Input, Modal, Select, Spinner, Switch, toast,
} from "@/components/ui";
import { formatDuration, formatNs } from "./format";
import {
  ConfirmIdentityModal, EnrollIdentityModal, MergeIdentityModal,
} from "@/components/identity/modals";
import { SpeakerDetailModal } from "./SpeakerDetailModal";
import { SimilarityMapModal } from "./SimilarityMapModal";

/** 说话人列表：状态/关键字过滤 + 确认/编辑/合并/删除/音频注册。 */
export function SpeakersPanel() {
  const { t } = useTranslation("sound");
  const queryClient = useQueryClient();
  const [status, setStatus] = useState("");
  const [keyword, setKeyword] = useState("");
  const [detailId, setDetailId] = useState<number | null>(null);
  const [confirmTarget, setConfirmTarget] = useState<SpeakerListItem | null>(null);
  const [mergeSource, setMergeSource] = useState<SpeakerListItem | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<SpeakerListItem | null>(null);
  const [enrollOpen, setEnrollOpen] = useState(false);
  const [pruneOpen, setPruneOpen] = useState(false);
  const [consolidateOpen, setConsolidateOpen] = useState(false);
  const [pruneInsignificant, setPruneInsignificant] = useState(true);
  const [simmapOpen, setSimmapOpen] = useState(false);

  const { data, isLoading, refetch } = useQuery({
    queryKey: ["audioSpeakers", status, keyword],
    queryFn: () =>
      audioApi.speakers({ status, keyword, limit: 100 }).then((r) => r.data),
    refetchInterval: 15_000,
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["audioSpeakers"] });

  const onError = (err: unknown) => {
    const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
    toast.error(msg || t("messages.opFailed"));
  };

  const confirmMutation = useMutation({
    mutationFn: ({ id, name, role }: { id: number; name: string; role: string }) =>
      audioApi.confirmSpeaker(id, name, role),
    onSuccess: () => {
      toast.success(t("messages.confirmSuccess"));
      setConfirmTarget(null);
      invalidate();
    },
    onError,
  });

  const mergeMutation = useMutation({
    mutationFn: (targetId: number) => audioApi.mergeSpeakers(mergeSource!.id, targetId),
    onSuccess: () => {
      toast.success(t("messages.mergeSuccess"));
      setMergeSource(null);
      invalidate();
    },
    onError,
  });

  const deleteMutation = useMutation({
    mutationFn: () => audioApi.deleteSpeaker(deleteTarget!.id),
    onSuccess: () => {
      toast.success(t("messages.deleteSuccess"));
      setDeleteTarget(null);
      invalidate();
    },
    onError,
  });

  const enrollMutation = useMutation({
    mutationFn: ({ name, role, file }: { name: string; role: string; file: File }) =>
      audioApi.enrollAudio(file, name, role),
    onSuccess: (r) => {
      toast.success(t("messages.enrollSuccess", { count: r.data.samples_enrolled }));
      setEnrollOpen(false);
      invalidate();
    },
    onError,
  });

  const items = data?.items ?? [];
  const mergeCandidates = items.filter((s) => s.id !== mergeSource?.id);
  // 归类：待确认（需处理）在前，已确认在后
  const pendingItems = items.filter((s) => s.status === "pending");
  const confirmedItems = items.filter((s) => s.status === "confirmed");

  const pruneMutation = useMutation({
    mutationFn: () => audioApi.pruneSpeakers(true),
    onSuccess: (r) => {
      toast.success(t("messages.pruneSuccess", { count: r.data.pruned }));
      setPruneOpen(false);
      invalidate();
    },
    onError,
  });

  const consolidatePreview = useMutation({
    mutationFn: () => audioApi.consolidateSpeakers({ dry_run: true }),
    onError,
  });

  const refineMut = useMutation({
    mutationFn: (id: number) => audioApi.refineSpeaker(id),
    onSuccess: (r) => {
      toast.success(t("messages.refineSuccess", {
        samples: r.data.samples,
        drift: r.data.anchor_similarity ?? t("messages.refineInitial"),
      }));
      invalidate();
    },
    onError,
  });

  const consolidateRun = useMutation({
    mutationFn: () =>
      audioApi.consolidateSpeakers({
        dry_run: false,
        prune_insignificant: pruneInsignificant,
      }),
    onSuccess: (r) => {
      toast.success(t("messages.consolidateSuccess", {
        clusters: r.data.merges.length,
        count: r.data.speakers_affected + r.data.pruned.length,
      }));
      setConsolidateOpen(false);
      consolidatePreview.reset();
      invalidate();
    },
    onError,
  });

  const renderCard = (s: SpeakerListItem) => (
    <Card
      key={s.id}
      title={s.name || s.speaker_key}
      subtitle={s.role || undefined}
      actions={
        <Badge variant={s.status === "confirmed" ? "ok" : "warn"}>
          {t(`status.${s.status}`)}
        </Badge>
      }
    >
      <div className="space-y-2 text-xs text-muted">
        <div className="flex gap-4">
          <span>{t("fields.samples")}: {s.sample_count}</span>
          <span>{t("fields.totalAudio")}: {formatDuration(s.total_audio_ms)}</span>
          <span>{t("fields.matches")}: {s.match_count}</span>
        </div>
        <div className="flex flex-wrap items-center gap-1">
          {Object.entries(s.channels ?? {}).map(([ch, n]) => (
            <Badge key={ch} variant="neutral">
              {t(`channels.${ch}`, ch)}×{n}
            </Badge>
          ))}
        </div>
        <div>{t("fields.lastSeen")}: {formatNs(s.last_seen_ns)}</div>
        <div className="flex items-center gap-1 pt-1">
          <Button size="sm" variant="secondary" onClick={() => setDetailId(s.id)}>
            {t("actions.detail")}
          </Button>
          {s.status === "pending" && (
            <Button
              size="sm" variant="secondary"
              onClick={() => setConfirmTarget(s)}
            >
              <UserCheck size={13} className="mr-1" />
              {t("actions.confirm")}
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={() => setDetailId(s.id)}>
            <Pencil size={13} />
          </Button>
          <Button
            size="sm" variant="ghost"
            onClick={() => setMergeSource(s)}
          >
            <GitMerge size={13} />
          </Button>
          <Button
            size="sm" variant="ghost"
            disabled={refineMut.isPending}
            title={t("actions.refineHint")}
            onClick={() => refineMut.mutate(s.id)}
          >
            <Focus size={13} />
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setDeleteTarget(s)}>
            <Trash2 size={13} />
          </Button>
        </div>
      </div>
    </Card>
  );

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">{t("filters.allStatus")}</option>
          <option value="confirmed">{t("filters.confirmed")}</option>
          <option value="pending">{t("filters.pending")}</option>
        </Select>
        <Input
          className="w-52"
          placeholder={t("filters.keyword")}
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
        />
        <Button variant="ghost" size="sm" onClick={() => refetch()}>
          <RefreshCw size={14} />
        </Button>
        <div className="flex-1" />
        <Button size="sm" onClick={() => setEnrollOpen(true)}>
          <Plus size={14} className="mr-1" />
          {t("actions.enroll")}
        </Button>
      </div>

      {isLoading ? (
        <div className="flex justify-center py-10"><Spinner /></div>
      ) : items.length === 0 ? (
        <EmptyState title={t("empty.speakers")} description={t("empty.speakersHint")} />
      ) : (
        <div className="space-y-4">
          {pendingItems.length > 0 && (
            <section className="space-y-2">
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold text-warn">
                  {t("groups.pending", { count: pendingItems.length })}
                </span>
                <div className="flex-1 border-t border-border" />
                <Button size="sm" variant="secondary" onClick={() => setSimmapOpen(true)}>
                  <Grid3X3 size={13} className="mr-1" />
                  {t("actions.simmap")}
                </Button>
                <Button
                  size="sm" variant="secondary"
                  loading={consolidatePreview.isPending}
                  onClick={() => {
                    setConsolidateOpen(true);
                    consolidatePreview.mutate();
                  }}
                >
                  <GitMerge size={13} className="mr-1" />
                  {t("actions.consolidate")}
                </Button>
                <Button
                  size="sm" variant="secondary"
                  onClick={() => setPruneOpen(true)}
                >
                  <Trash2 size={13} className="mr-1" />
                  {t("actions.prunePending")}
                </Button>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                {pendingItems.map(renderCard)}
              </div>
            </section>
          )}
          {confirmedItems.length > 0 && (
            <section className="space-y-2">
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold text-muted">
                  {t("groups.confirmed", { count: confirmedItems.length })}
                </span>
                <div className="flex-1 border-t border-border" />
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                {confirmedItems.map(renderCard)}
              </div>
            </section>
          )}
        </div>
      )}

      {detailId !== null && (
        <SpeakerDetailModal speakerId={detailId} onClose={() => setDetailId(null)} />
      )}

      <SimilarityMapModal open={simmapOpen} onClose={() => setSimmapOpen(false)} />

      {/* 确认临时说话人 */}
      <ConfirmIdentityModal
        open={confirmTarget !== null}
        onClose={() => setConfirmTarget(null)}
        pending={confirmMutation.isPending}
        onSubmit={(name, role) =>
          confirmMutation.mutate({ id: confirmTarget!.id, name, role })}
        labels={{
          title: t("modals.confirmTitle", { key: confirmTarget?.speaker_key ?? "" }),
          namePlaceholder: t("fields.name"),
          rolePlaceholder: t("fields.rolePlaceholder"),
          confirmLabel: t("actions.confirm"),
        }}
      />

      {/* 身份合并 */}
      <MergeIdentityModal
        source={mergeSource}
        candidates={mergeCandidates}
        getOptionLabel={(s) => `${s.name || s.speaker_key}（${t(`status.${s.status}`)}）`}
        onClose={() => setMergeSource(null)}
        onSubmit={(targetId) => mergeMutation.mutate(targetId)}
        pending={mergeMutation.isPending}
        labels={{
          title: t("modals.mergeTitle", { key: mergeSource?.speaker_key ?? "" }),
          hint: t("modals.mergeHint"),
          targetPlaceholder: t("modals.mergeTarget"),
          mergeLabel: t("actions.merge"),
        }}
      />

      {/* 音频注册 */}
      <EnrollIdentityModal
        open={enrollOpen}
        onClose={() => setEnrollOpen(false)}
        pending={enrollMutation.isPending}
        onSubmit={(name, role, file) => enrollMutation.mutate({ name, role, file })}
        accept="audio/*,.wav,.mp3,.m4a,.flac,.ogg"
        labels={{
          title: t("modals.enrollTitle"),
          namePlaceholder: t("fields.name"),
          rolePlaceholder: t("fields.rolePlaceholder"),
          hint: t("modals.enrollHint"),
          enrollLabel: t("actions.enroll"),
        }}
      />

      <ConfirmDialog
        open={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => deleteMutation.mutate()}
        title={t("modals.deleteTitle")}
        message={t("modals.deleteHint", { key: deleteTarget?.speaker_key ?? "" })}
        danger
        loading={deleteMutation.isPending}
      />

      <ConfirmDialog
        open={pruneOpen}
        onClose={() => setPruneOpen(false)}
        onConfirm={() => pruneMutation.mutate()}
        title={t("modals.pruneTitle")}
        message={t("modals.pruneHint", { count: pendingItems.length })}
        danger
        loading={pruneMutation.isPending}
      />

      {/* 智能合并预览：分簇确认后执行 */}
      <Modal
        open={consolidateOpen}
        onClose={() => { setConsolidateOpen(false); consolidatePreview.reset(); }}
        title={t("modals.consolidateTitle")}
        width="max-w-2xl"
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => { setConsolidateOpen(false); consolidatePreview.reset(); }}
            >
              {t("common:cancel")}
            </Button>
            <Button
              loading={consolidateRun.isPending}
              disabled={!consolidatePreview.data?.data.cluster_count}
              onClick={() => consolidateRun.mutate()}
            >
              {t("actions.consolidateRun", {
                count: consolidatePreview.data?.data.speakers_affected ?? 0,
              })}
            </Button>
          </>
        }
      >
        {consolidatePreview.isPending ? (
          <div className="flex justify-center py-8"><Spinner /></div>
        ) : !consolidatePreview.data?.data.cluster_count
            && !consolidatePreview.data?.data.insignificant.length ? (
          <p className="text-sm text-muted py-2">{t("modals.consolidateEmpty")}</p>
        ) : (
          <div className="space-y-3">
            <p className="text-xs text-muted">
              {t("modals.consolidateHint", {
                threshold: consolidatePreview.data.data.threshold,
              })}
            </p>
            {consolidatePreview.data.data.clusters.map((cluster, i) => (
              <div key={i} className="rounded-md border border-border px-3 py-2 space-y-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  {cluster.members.map((m) => (
                    <Badge key={m.id} variant={m.id === cluster.keep_id ? "ok" : "neutral"}>
                      {m.name || m.speaker_key}
                      {m.id === cluster.keep_id ? ` ${t("modals.consolidateKeep")}` : ""}
                      {" "}({m.similarity.toFixed(2)})
                    </Badge>
                  ))}
                </div>
              </div>
            ))}
            {/* 低价值清理（环境音/路人：命中少+时长短） */}
            {(consolidatePreview.data.data.insignificant.length ?? 0) > 0 && (
              <div className="rounded-md border border-warn/40 bg-warn-subtle px-3 py-2 space-y-2">
                <label className="flex items-center gap-2 text-xs text-foreground">
                  <Switch
                    checked={pruneInsignificant}
                    onChange={setPruneInsignificant}
                  />
                  {t("modals.insignificantTitle", {
                    count: consolidatePreview.data.data.insignificant.length,
                    matches: consolidatePreview.data.data.insignificant_limits.max_matches,
                    seconds: Math.round(
                      consolidatePreview.data.data.insignificant_limits.max_audio_ms / 1000),
                  })}
                </label>
                <div className="flex flex-wrap gap-1.5">
                  {consolidatePreview.data.data.insignificant.map((s) => (
                    <Badge key={s.id} variant="warn">
                      {s.name || s.speaker_key}
                      {" "}({t("matchSummary", { count: s.match_count, seconds: Math.round(s.total_audio_ms / 1000) })})
                    </Badge>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}
