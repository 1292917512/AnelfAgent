import { useTranslation } from "react-i18next";
import { Focus, GitMerge, Grid3X3, Pencil, Plus, RefreshCw, Trash2, UserCheck } from "lucide-react";
import type { SpeakerListItem } from "./types";
import { Card } from "@/components/common/Card";
import { Pagination } from "@/components/common/Pagination";
import { QueryError } from "@/components/common/AsyncState";
import { Badge, Button, EmptyState, Input, Select, Spinner } from "@/components/ui";
import { formatDuration, formatNs } from "./format";
import { useSpeakers } from "./useSpeakers";
import { SpeakerDialogs } from "./SpeakerDialogs";

/** 身份管理列表：筛选、分页与操作入口。 */
export function SpeakersPanel() {
  const { t } = useTranslation("sound");
  const state = useSpeakers();
  const {
    setDetailId, setConfirmTarget, setMergeSource, setDeleteTarget, setEnrollOpen, setPruneOpen,
    setConsolidateOpen, setSimmapOpen, items, pendingItems, confirmedItems, consolidatePreview,
    refineMut, data, isLoading, isFetching, isPlaceholderData, error,
    refetch, status, setStatus, keyword, setKeyword, page,
    pageSize, setPage,
  } = state;
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
          <Button size="sm" variant="ghost" title={t("common:edit")} onClick={() => setDetailId(s.id)}>
            <Pencil size={13} />
          </Button>
          <Button
            size="sm" variant="ghost"
            title={t("common:merge")} onClick={() => setMergeSource(s)}
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
          <Button size="sm" variant="ghost" title={t("common:delete")} onClick={() => setDeleteTarget(s)}>
            <Trash2 size={13} />
          </Button>
        </div>
      </div>
    </Card>
  );

  return (
    <div className="space-y-3" aria-busy={isFetching}>
      <div className="flex flex-wrap items-center gap-2">
        <Select aria-label={t("filters.allStatus")} value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">{t("filters.allStatus")}</option>
          <option value="confirmed">{t("filters.confirmed")}</option>
          <option value="pending">{t("filters.pending")}</option>
        </Select>
        <Input
          className="w-52"
          aria-label={t("filters.keyword")} placeholder={t("filters.keyword")}
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

      {error && <QueryError compact error={error} retry={() => void refetch()} />}
      {isFetching && !isLoading && <p role="status" className="text-xs text-muted">{t("common:loading")}</p>}
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

      <Pagination page={page} total={data?.total ?? 0} pageSize={pageSize} onChange={setPage} disabled={isPlaceholderData} />
      <SpeakerDialogs state={state} />
    </div>
  );
}
