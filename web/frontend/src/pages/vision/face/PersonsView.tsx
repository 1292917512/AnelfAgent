import { useTranslation } from "react-i18next";
import { Focus, GitMerge, Link2, Plus, RefreshCw, ScanSearch, Trash2, UserCheck } from "lucide-react";
import type { FacePerson } from "@/lib/types";
import { Card } from "@/components/common/Card";
import { Pagination } from "@/components/common/Pagination";
import { QueryError } from "@/components/common/AsyncState";
import { Badge, Button, EmptyState, Input, Select, Spinner } from "@/components/ui";
import { formatNs } from "./format";
import { usePersons } from "./usePersons";
import { PersonDialogs } from "./PersonDialogs";

/** 身份管理列表：筛选、分页与操作入口。 */
export function PersonsView() {
  const { t } = useTranslation("vision");
  const state = usePersons();
  const {
    setDetailId, setConfirmTarget, setMergeSource, setDeleteTarget, setBindTarget, setBindScope,
    setEnrollOpen, setIdentifyOpen, setPruneOpen, setConsolidateOpen, refineMut, consolidatePreview,
    items, pendingItems, confirmedItems, data, isLoading, isFetching,
    isPlaceholderData, error, refetch, status, setStatus, keyword,
    setKeyword, page, pageSize, setPage,
  } = state;
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
              onClick={() => setConfirmTarget(p)}>
              <UserCheck size={13} className="mr-1" />
              {t("face.actions.confirm")}
            </Button>
          )}
          <Button size="sm" variant="ghost" title={t("face.actions.bind")}
            onClick={() => { setBindTarget(p); setBindScope(p.entity_scope || ""); }}>
            <Link2 size={13} />
          </Button>
          <Button size="sm" variant="ghost"
            title={t("common:merge")} onClick={() => setMergeSource(p)}>
            <GitMerge size={13} />
          </Button>
          <Button size="sm" variant="ghost" disabled={refineMut.isPending}
            title={t("face.actions.refineHint")} onClick={() => refineMut.mutate(p.id)}>
            <Focus size={13} />
          </Button>
          <Button size="sm" variant="ghost" title={t("common:delete")} onClick={() => setDeleteTarget(p)}>
            <Trash2 size={13} />
          </Button>
        </div>
      </div>
    </Card>
  );

  return (
    <div className="space-y-3" aria-busy={isFetching}>
      <div className="flex flex-wrap items-center gap-2">
        <Select aria-label={t("face.filters.allStatus")} value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">{t("face.filters.allStatus")}</option>
          <option value="confirmed">{t("face.filters.confirmed")}</option>
          <option value="pending">{t("face.filters.pending")}</option>
        </Select>
        <Input className="w-52" aria-label={t("face.filters.keyword")} placeholder={t("face.filters.keyword")}
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

      {error && <QueryError compact error={error} retry={() => void refetch()} />}
      {isFetching && !isLoading && <p role="status" className="text-xs text-muted">{t("common:loading")}</p>}
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

      <Pagination page={page} total={data?.total ?? 0} pageSize={pageSize} onChange={setPage} disabled={isPlaceholderData} />
      <PersonDialogs state={state} />
    </div>
  );
}
