import { useTranslation } from "react-i18next";
import { Badge, Button, ConfirmDialog, Modal, Spinner, Switch, Input } from "@/components/ui";
import { Pagination } from "@/components/common/Pagination";
import { QueryError } from "@/components/common/AsyncState";
import { ConfirmIdentityModal, EnrollIdentityModal, MergeIdentityModal } from "@/components/identity/modals";
import { SpeakerDetailModal } from "./SpeakerDetailModal";
import type { useSpeakers } from "./useSpeakers";
import { SimilarityMapModal } from "./SimilarityMapModal";

/** 身份操作弹窗组合，保持表单组件身份稳定。 */
export function SpeakerDialogs({ state }: { state: ReturnType<typeof useSpeakers> }) {
  const { t } = useTranslation("sound");
  const {
    detailId, setDetailId, confirmTarget, setConfirmTarget, mergeSource, setMergeSource,
    deleteTarget, setDeleteTarget, enrollOpen, setEnrollOpen, pruneOpen, setPruneOpen,
    consolidateOpen, setConsolidateOpen, pruneInsignificant, setPruneInsignificant, simmapOpen, setSimmapOpen,
    confirmMutation, mergeMutation, deleteMutation, enrollMutation, mergeCandidates, pendingItems,
    pruneMutation, consolidatePreview, consolidateRun,
  } = state;
  const { mergeFilters, mergeQuery } = state;
  return <>
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
        candidateControls={<>
          <Input aria-label={t("common:search")} placeholder={t("common:search")} value={mergeFilters.keyword} onChange={(event) => mergeFilters.setKeyword(event.target.value)} />
          {mergeQuery.error && <QueryError compact error={mergeQuery.error} retry={() => void mergeQuery.refetch()} />}
          <Pagination page={mergeFilters.page} pageSize={mergeFilters.pageSize} total={mergeQuery.data?.total ?? 0}
            onChange={mergeFilters.setPage} disabled={mergeQuery.isPlaceholderData} />
        </>}
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
  </>;
}
