import { useTranslation } from "react-i18next";
import { Badge, Button, ConfirmDialog, Modal, Spinner, Switch, Input } from "@/components/ui";
import { Pagination } from "@/components/common/Pagination";
import { QueryError } from "@/components/common/AsyncState";
import { ConfirmIdentityModal, EnrollIdentityModal, MergeIdentityModal } from "@/components/identity/modals";
import { PersonDetailModal } from "./PersonDetailModal";
import type { usePersons } from "./usePersons";

/** 身份操作弹窗组合，保持表单组件身份稳定。 */
export function PersonDialogs({ state }: { state: ReturnType<typeof usePersons> }) {
  const { t } = useTranslation("vision");
  const {
    detailId, setDetailId, confirmTarget, setConfirmTarget, mergeSource, setMergeSource,
    deleteTarget, setDeleteTarget, bindTarget, setBindTarget, bindScope, setBindScope,
    enrollOpen, setEnrollOpen, enrollScope, setEnrollScope, identifyOpen, setIdentifyOpen,
    identifyFile, setIdentifyFile, identifyIngest, setIdentifyIngest, pruneOpen, setPruneOpen,
    consolidateOpen, setConsolidateOpen, pruneInsignificant, setPruneInsignificant, invalidate, confirmMutation,
    mergeMutation, bindMutation, deleteMutation, enrollMutation, identifyMutation, pruneMutation,
    consolidatePreview, consolidateRun, mergeCandidates, pendingItems,
  } = state;
  const { mergeFilters, mergeQuery } = state;
  return <>
      {detailId !== null && (
        <PersonDetailModal personId={detailId} onClose={() => { setDetailId(null); invalidate(); }} />
      )}

      {/* 确认临时人物 */}
      <ConfirmIdentityModal
        open={confirmTarget !== null}
        onClose={() => setConfirmTarget(null)}
        pending={confirmMutation.isPending}
        onSubmit={(name, role) =>
          confirmMutation.mutate({ id: confirmTarget!.id, name, role })}
        labels={{
          title: t("face.modals.confirmTitle", { key: confirmTarget?.person_key ?? "" }),
          namePlaceholder: t("face.fields.name"),
          rolePlaceholder: t("face.fields.rolePlaceholder"),
          confirmLabel: t("face.actions.confirm"),
        }}
      />

      {/* 实体绑定 */}
      <Modal open={bindTarget !== null} onClose={() => setBindTarget(null)}
        title={t("face.modals.bindTitle", { name: bindTarget?.name || bindTarget?.person_key || "" })}
        footer={
          <>
            <Button variant="secondary" onClick={() => setBindTarget(null)}>
              {t("common:cancel")}
            </Button>
            <Button loading={bindMutation.isPending} onClick={() => bindMutation.mutate()}>
              {t("face.actions.bind")}
            </Button>
          </>
        }>
        <div className="space-y-3">
          <p className="text-xs text-muted">{t("face.modals.bindHint")}</p>
          <Input className="font-mono" placeholder="user:qq:456" value={bindScope}
            onChange={(e) => setBindScope(e.target.value)} />
        </div>
      </Modal>

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
        getOptionLabel={(p) => `${p.name || p.person_key}（${t(`face.status.${p.status}`)}）`}
        onClose={() => setMergeSource(null)}
        onSubmit={(targetId) => mergeMutation.mutate(targetId)}
        pending={mergeMutation.isPending}
        labels={{
          title: t("face.modals.mergeTitle", { key: mergeSource?.person_key ?? "" }),
          hint: t("face.modals.mergeHint"),
          targetPlaceholder: t("face.modals.mergeTarget"),
          mergeLabel: t("face.actions.merge"),
        }}
      />

      {/* 图片注册 */}
      <EnrollIdentityModal
        open={enrollOpen}
        onClose={() => setEnrollOpen(false)}
        pending={enrollMutation.isPending}
        onSubmit={(name, role, file) => enrollMutation.mutate({ name, role, file })}
        accept="image/*"
        scope={{ value: enrollScope, onChange: setEnrollScope }}
        labels={{
          title: t("face.modals.enrollTitle"),
          namePlaceholder: t("face.fields.name"),
          rolePlaceholder: t("face.fields.rolePlaceholder"),
          scopePlaceholder: t("face.fields.scopePlaceholder"),
          hint: t("face.modals.enrollHint"),
          enrollLabel: t("face.actions.enroll"),
        }}
      />

      {/* 上传识别 */}
      <Modal open={identifyOpen} onClose={() => setIdentifyOpen(false)}
        title={t("face.modals.identifyTitle")}
        footer={
          <>
            <Button variant="secondary" onClick={() => setIdentifyOpen(false)}>
              {t("common:cancel")}
            </Button>
            <Button loading={identifyMutation.isPending} disabled={!identifyFile}
              onClick={() => identifyMutation.mutate()}>
              {t("face.actions.identify")}
            </Button>
          </>
        }>
        <div className="space-y-3">
          <input type="file" accept="image/*"
            className="text-sm text-muted file:mr-3 file:rounded-md file:border file:border-border file:bg-elevated file:px-3 file:py-1.5 file:text-sm file:text-foreground"
            onChange={(e) => setIdentifyFile(e.target.files?.[0] ?? null)} />
          <label className="flex items-center gap-2 text-xs text-foreground">
            <Switch checked={identifyIngest} onChange={setIdentifyIngest} />
            {t("face.modals.identifyIngest")}
          </label>
          {identifyMutation.data?.data.faces && (
            <div className="space-y-1.5 pt-2 border-t border-border">
              {identifyMutation.data.data.faces.map((f) => (
                <div key={f.index} className="text-xs flex items-center gap-2">
                  <Badge variant={f.best_match?.matched ? "ok" : "neutral"}>
                    #{f.index} {(f.det_score * 100).toFixed(0)}%
                  </Badge>
                  {f.skipped
                    ? <span className="text-muted">{f.skipped}</span>
                    : f.best_match
                      ? <span>{f.best_match.name || f.best_match.person_key}
                          {" "}({f.best_match.similarity.toFixed(3)})</span>
                      : <span className="text-muted">{t("face.identify.noMatch")}</span>}
                </div>
              ))}
            </div>
          )}
        </div>
      </Modal>

      <ConfirmDialog open={deleteTarget !== null} onClose={() => setDeleteTarget(null)}
        onConfirm={() => deleteMutation.mutate()}
        title={t("face.modals.deleteTitle")}
        message={t("face.modals.deleteHint", { key: deleteTarget?.person_key ?? "" })}
        danger loading={deleteMutation.isPending} />

      <ConfirmDialog open={pruneOpen} onClose={() => setPruneOpen(false)}
        onConfirm={() => pruneMutation.mutate()}
        title={t("face.modals.pruneTitle")}
        message={t("face.modals.pruneHint", { count: pendingItems.length })}
        danger loading={pruneMutation.isPending} />

      {/* 智能合并预览 */}
      <Modal open={consolidateOpen}
        onClose={() => { setConsolidateOpen(false); consolidatePreview.reset(); }}
        title={t("face.modals.consolidateTitle")} width="max-w-2xl"
        footer={
          <>
            <Button variant="secondary"
              onClick={() => { setConsolidateOpen(false); consolidatePreview.reset(); }}>
              {t("common:cancel")}
            </Button>
            <Button loading={consolidateRun.isPending}
              disabled={!consolidatePreview.data?.data.cluster_count}
              onClick={() => consolidateRun.mutate()}>
              {t("face.actions.consolidateRun", {
                count: consolidatePreview.data?.data.persons_affected ?? 0 })}
            </Button>
          </>
        }>
        {consolidatePreview.isPending ? (
          <div className="flex justify-center py-8"><Spinner /></div>
        ) : !consolidatePreview.data?.data.cluster_count
            && !consolidatePreview.data?.data.insignificant.length ? (
          <p className="text-sm text-muted py-2">{t("face.modals.consolidateEmpty")}</p>
        ) : (
          <div className="space-y-3">
            <p className="text-xs text-muted">
              {t("face.modals.consolidateHint", { threshold: consolidatePreview.data.data.threshold })}
            </p>
            {consolidatePreview.data.data.clusters.map((cluster, i) => (
              <div key={i} className="rounded-md border border-border px-3 py-2 space-y-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  {cluster.members.map((m) => (
                    <Badge key={m.id} variant={m.id === cluster.keep_id ? "ok" : "neutral"}>
                      {m.name || m.person_key}
                      {m.id === cluster.keep_id ? ` ${t("face.modals.consolidateKeep")}` : ""}
                      {" "}({m.similarity.toFixed(2)})
                    </Badge>
                  ))}
                </div>
              </div>
            ))}
            {(consolidatePreview.data.data.insignificant.length ?? 0) > 0 && (
              <div className="rounded-md border border-warn/40 bg-warn-subtle px-3 py-2 space-y-2">
                <label className="flex items-center gap-2 text-xs text-foreground">
                  <Switch checked={pruneInsignificant} onChange={setPruneInsignificant} />
                  {t("face.modals.insignificantTitle", {
                    count: consolidatePreview.data.data.insignificant.length,
                    matches: consolidatePreview.data.data.insignificant_limits.max_matches,
                  })}
                </label>
                <div className="flex flex-wrap gap-1.5">
                  {consolidatePreview.data.data.insignificant.map((p) => (
                    <Badge key={p.id} variant="warn">
                      {p.name || p.person_key} ({t("face.matchCount", { count: p.match_count })})
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
