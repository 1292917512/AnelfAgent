import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  Focus, GitMerge, Link2, Plus, RefreshCw, ScanSearch, Trash2, UserCheck,
} from "lucide-react";
import { faceApi, type FacePerson } from "@/lib/api";
import { Card } from "@/components/common/Card";
import {
  Badge, Button, ConfirmDialog, EmptyState, Input, Modal, Select, Spinner, Switch, toast,
} from "@/components/ui";
import { formatNs } from "./format";
import { PersonDetailModal } from "./PersonDetailModal";

/** 人物列表：状态/关键字过滤 + 注册/识别/确认/编辑/绑定/合并/删除。 */
export function PersonsView() {
  const { t } = useTranslation("vision");
  const queryClient = useQueryClient();
  const [status, setStatus] = useState("");
  const [keyword, setKeyword] = useState("");
  const [detailId, setDetailId] = useState<number | null>(null);
  const [confirmTarget, setConfirmTarget] = useState<FacePerson | null>(null);
  const [confirmName, setConfirmName] = useState("");
  const [confirmRole, setConfirmRole] = useState("");
  const [mergeSource, setMergeSource] = useState<FacePerson | null>(null);
  const [mergeTargetId, setMergeTargetId] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<FacePerson | null>(null);
  const [bindTarget, setBindTarget] = useState<FacePerson | null>(null);
  const [bindScope, setBindScope] = useState("");
  const [enrollOpen, setEnrollOpen] = useState(false);
  const [enrollName, setEnrollName] = useState("");
  const [enrollRole, setEnrollRole] = useState("");
  const [enrollScope, setEnrollScope] = useState("");
  const [enrollFile, setEnrollFile] = useState<File | null>(null);
  const [identifyOpen, setIdentifyOpen] = useState(false);
  const [identifyFile, setIdentifyFile] = useState<File | null>(null);
  const [identifyIngest, setIdentifyIngest] = useState(true);
  const [pruneOpen, setPruneOpen] = useState(false);
  const [consolidateOpen, setConsolidateOpen] = useState(false);
  const [pruneInsignificant, setPruneInsignificant] = useState(true);

  const { data, isLoading, refetch } = useQuery({
    queryKey: ["facePersons", status, keyword],
    queryFn: () => faceApi.persons({ status, keyword, limit: 100 }).then((r) => r.data),
    refetchInterval: 15_000,
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["facePersons"] });
    queryClient.invalidateQueries({ queryKey: ["faceStatus"] });
    queryClient.invalidateQueries({ queryKey: ["faceEvents"] });
  };

  const onError = (err: unknown) => {
    const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
    toast.error(msg || t("face.messages.opFailed"));
  };

  const confirmMutation = useMutation({
    mutationFn: () => faceApi.confirmPerson(confirmTarget!.id, confirmName, confirmRole),
    onSuccess: () => {
      toast.success(t("face.messages.confirmSuccess"));
      setConfirmTarget(null); setConfirmName(""); setConfirmRole("");
      invalidate();
    },
    onError,
  });

  const mergeMutation = useMutation({
    mutationFn: () => faceApi.mergePersons(mergeSource!.id, Number(mergeTargetId)),
    onSuccess: () => {
      toast.success(t("face.messages.mergeSuccess"));
      setMergeSource(null); setMergeTargetId("");
      invalidate();
    },
    onError,
  });

  const bindMutation = useMutation({
    mutationFn: () => faceApi.bindPerson(bindTarget!.id, bindScope),
    onSuccess: () => {
      toast.success(t("face.messages.bindSuccess"));
      setBindTarget(null); setBindScope("");
      invalidate();
    },
    onError,
  });

  const deleteMutation = useMutation({
    mutationFn: () => faceApi.deletePerson(deleteTarget!.id),
    onSuccess: () => {
      toast.success(t("face.messages.deleteSuccess"));
      setDeleteTarget(null);
      invalidate();
    },
    onError,
  });

  const enrollMutation = useMutation({
    mutationFn: () => faceApi.enrollImage(enrollFile!, enrollName,
      { role: enrollRole, entityScope: enrollScope }),
    onSuccess: () => {
      toast.success(t("face.messages.enrollSuccess"));
      setEnrollOpen(false); setEnrollName(""); setEnrollRole("");
      setEnrollScope(""); setEnrollFile(null);
      invalidate();
    },
    onError,
  });

  const identifyMutation = useMutation({
    mutationFn: () => faceApi.identifyImage(identifyFile!, identifyIngest),
    onSuccess: (r) => {
      const faces = r.data.faces?.length ?? r.data.hits?.length ?? 0;
      toast.success(t("face.messages.identifySuccess", { count: faces }));
      if (identifyIngest) {
        setIdentifyOpen(false); setIdentifyFile(null);
        invalidate();
      }
    },
    onError,
  });

  const pruneMutation = useMutation({
    mutationFn: () => faceApi.prunePersons(true),
    onSuccess: (r) => {
      toast.success(t("face.messages.pruneSuccess", { count: r.data.pruned }));
      setPruneOpen(false);
      invalidate();
    },
    onError,
  });

  const refineMut = useMutation({
    mutationFn: (id: number) => faceApi.refinePerson(id),
    onSuccess: (r) => {
      toast.success(t("face.messages.refineSuccess", {
        samples: r.data.samples,
        drift: r.data.anchor_similarity ?? t("face.messages.refineInitial"),
      }));
      invalidate();
    },
    onError,
  });

  const consolidatePreview = useMutation({
    mutationFn: () => faceApi.consolidatePersons({ dry_run: true }),
    onError,
  });

  const consolidateRun = useMutation({
    mutationFn: () => faceApi.consolidatePersons({
      dry_run: false, prune_insignificant: pruneInsignificant }),
    onSuccess: (r) => {
      toast.success(t("face.messages.consolidateSuccess", {
        clusters: r.data.merges.length,
        count: r.data.persons_affected + r.data.pruned.length,
      }));
      setConsolidateOpen(false);
      consolidatePreview.reset();
      invalidate();
    },
    onError,
  });

  const items = data?.items ?? [];
  const mergeCandidates = items.filter((p) => p.id !== mergeSource?.id);
  const pendingItems = items.filter((p) => p.status === "pending");
  const confirmedItems = items.filter((p) => p.status === "confirmed");

  const renderCard = (p: FacePerson) => (
    <Card
      key={p.id}
      title={p.name || p.person_key}
      subtitle={p.role || undefined}
      actions={
        <Badge variant={p.status === "confirmed" ? "ok" : "warn"}>
          {t(`face.status.${p.status}`)}
        </Badge>
      }
    >
      <div className="space-y-2 text-xs text-muted">
        <div className="flex gap-4">
          <span>{t("face.fields.samples")}: {p.sample_count ?? 0}</span>
          <span>{t("face.fields.matches")}: {p.match_count}</span>
        </div>
        {p.entity_scope
          ? <div className="flex items-center gap-1">
              <Link2 size={12} className="text-accent" />
              <span className="font-mono text-[11px] text-accent">{p.entity_scope}</span>
            </div>
          : <div className="text-[11px] text-muted">{t("face.fields.unbound")}</div>}
        <div>{t("face.fields.lastSeen")}: {formatNs(p.last_seen_ns)}</div>
        <div className="flex items-center gap-1 pt-1 flex-wrap">
          <Button size="sm" variant="secondary" onClick={() => setDetailId(p.id)}>
            {t("face.actions.detail")}
          </Button>
          {p.status === "pending" && (
            <Button size="sm" variant="secondary"
              onClick={() => { setConfirmTarget(p); setConfirmName(""); setConfirmRole(""); }}>
              <UserCheck size={13} className="mr-1" />
              {t("face.actions.confirm")}
            </Button>
          )}
          <Button size="sm" variant="ghost" title={t("face.actions.bind")}
            onClick={() => { setBindTarget(p); setBindScope(p.entity_scope || ""); }}>
            <Link2 size={13} />
          </Button>
          <Button size="sm" variant="ghost"
            onClick={() => { setMergeSource(p); setMergeTargetId(""); }}>
            <GitMerge size={13} />
          </Button>
          <Button size="sm" variant="ghost" disabled={refineMut.isPending}
            title={t("face.actions.refineHint")} onClick={() => refineMut.mutate(p.id)}>
            <Focus size={13} />
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setDeleteTarget(p)}>
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
          <option value="">{t("face.filters.allStatus")}</option>
          <option value="confirmed">{t("face.filters.confirmed")}</option>
          <option value="pending">{t("face.filters.pending")}</option>
        </Select>
        <Input className="w-52" placeholder={t("face.filters.keyword")}
          value={keyword} onChange={(e) => setKeyword(e.target.value)} />
        <Button variant="ghost" size="sm" onClick={() => refetch()}>
          <RefreshCw size={14} />
        </Button>
        <div className="flex-1" />
        <Button size="sm" variant="secondary" onClick={() => setIdentifyOpen(true)}>
          <ScanSearch size={14} className="mr-1" />
          {t("face.actions.identify")}
        </Button>
        <Button size="sm" onClick={() => setEnrollOpen(true)}>
          <Plus size={14} className="mr-1" />
          {t("face.actions.enroll")}
        </Button>
      </div>

      {isLoading ? (
        <div className="flex justify-center py-10"><Spinner /></div>
      ) : items.length === 0 ? (
        <EmptyState title={t("face.empty.persons")} description={t("face.empty.personsHint")} />
      ) : (
        <div className="space-y-4">
          {pendingItems.length > 0 && (
            <section className="space-y-2">
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold text-warn">
                  {t("face.groups.pending", { count: pendingItems.length })}
                </span>
                <div className="flex-1 border-t border-border" />
                <Button size="sm" variant="secondary" loading={consolidatePreview.isPending}
                  onClick={() => { setConsolidateOpen(true); consolidatePreview.mutate(); }}>
                  <GitMerge size={13} className="mr-1" />
                  {t("face.actions.consolidate")}
                </Button>
                <Button size="sm" variant="secondary" onClick={() => setPruneOpen(true)}>
                  <Trash2 size={13} className="mr-1" />
                  {t("face.actions.prunePending")}
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
                  {t("face.groups.confirmed", { count: confirmedItems.length })}
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
        <PersonDetailModal personId={detailId} onClose={() => { setDetailId(null); invalidate(); }} />
      )}

      {/* 确认临时人物 */}
      <Modal open={confirmTarget !== null} onClose={() => setConfirmTarget(null)}
        title={t("face.modals.confirmTitle", { key: confirmTarget?.person_key ?? "" })}
        footer={
          <>
            <Button variant="secondary" onClick={() => setConfirmTarget(null)}>
              {t("common:cancel")}
            </Button>
            <Button loading={confirmMutation.isPending} disabled={!confirmName.trim()}
              onClick={() => confirmMutation.mutate()}>
              {t("face.actions.confirm")}
            </Button>
          </>
        }>
        <div className="space-y-3">
          <Input placeholder={t("face.fields.name")} value={confirmName}
            onChange={(e) => setConfirmName(e.target.value)} />
          <Input placeholder={t("face.fields.rolePlaceholder")} value={confirmRole}
            onChange={(e) => setConfirmRole(e.target.value)} />
        </div>
      </Modal>

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
      <Modal open={mergeSource !== null} onClose={() => setMergeSource(null)}
        title={t("face.modals.mergeTitle", { key: mergeSource?.person_key ?? "" })}
        footer={
          <>
            <Button variant="secondary" onClick={() => setMergeSource(null)}>
              {t("common:cancel")}
            </Button>
            <Button variant="danger" loading={mergeMutation.isPending}
              disabled={!mergeTargetId} onClick={() => mergeMutation.mutate()}>
              {t("face.actions.merge")}
            </Button>
          </>
        }>
        <div className="space-y-3 text-sm">
          <p className="text-muted">{t("face.modals.mergeHint")}</p>
          <Select className="w-full" value={mergeTargetId}
            onChange={(e) => setMergeTargetId(e.target.value)}>
            <option value="">{t("face.modals.mergeTarget")}</option>
            {mergeCandidates.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name || p.person_key}（{t(`face.status.${p.status}`)}）
              </option>
            ))}
          </Select>
        </div>
      </Modal>

      {/* 图片注册 */}
      <Modal open={enrollOpen} onClose={() => setEnrollOpen(false)}
        title={t("face.modals.enrollTitle")}
        footer={
          <>
            <Button variant="secondary" onClick={() => setEnrollOpen(false)}>
              {t("common:cancel")}
            </Button>
            <Button loading={enrollMutation.isPending}
              disabled={!enrollName.trim() || !enrollFile}
              onClick={() => enrollMutation.mutate()}>
              {t("face.actions.enroll")}
            </Button>
          </>
        }>
        <div className="space-y-3">
          <Input placeholder={t("face.fields.name")} value={enrollName}
            onChange={(e) => setEnrollName(e.target.value)} />
          <Input placeholder={t("face.fields.rolePlaceholder")} value={enrollRole}
            onChange={(e) => setEnrollRole(e.target.value)} />
          <Input className="font-mono" placeholder={t("face.fields.scopePlaceholder")}
            value={enrollScope} onChange={(e) => setEnrollScope(e.target.value)} />
          <input type="file" accept="image/*"
            className="text-sm text-muted file:mr-3 file:rounded-md file:border file:border-border file:bg-elevated file:px-3 file:py-1.5 file:text-sm file:text-foreground"
            onChange={(e) => setEnrollFile(e.target.files?.[0] ?? null)} />
          <p className="text-xs text-muted">{t("face.modals.enrollHint")}</p>
        </div>
      </Modal>

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
                      {p.name || p.person_key} ({p.match_count}次)
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
