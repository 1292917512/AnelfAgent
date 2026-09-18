import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { CheckCheck, ChevronLeft, ChevronRight, RefreshCw, Trash2 } from "lucide-react";
import { faceApi, type FaceEvent } from "@/lib/api";
import { Badge, Button, EmptyState, Input, Spinner, Switch, toast } from "@/components/ui";
import { formatNs, onImgError } from "./format";

const PAGE = 20;

/** 出现事件时间线：在哪见过谁。按来源/未读过滤，可标记已读与删除。 */
export function EventsView() {
  const { t } = useTranslation("vision");
  const queryClient = useQueryClient();
  const [source, setSource] = useState("");
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [offset, setOffset] = useState(0);

  const { data, isLoading, refetch } = useQuery({
    queryKey: ["faceEvents", source, unreadOnly, offset],
    queryFn: () => faceApi.events({
      source: source || undefined, unread_only: unreadOnly, limit: PAGE, offset,
    }).then((r) => r.data),
    refetchInterval: 15_000,
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["faceEvents"] });
    queryClient.invalidateQueries({ queryKey: ["faceStatus"] });
  };
  const onError = (err: unknown) => {
    const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
    toast.error(msg || t("face.messages.opFailed"));
  };

  const markReadMut = useMutation({
    mutationFn: () => faceApi.markEventsRead(undefined, true),
    onSuccess: () => { toast.success(t("face.messages.markReadSuccess")); invalidate(); },
    onError,
  });
  const deleteMut = useMutation({
    mutationFn: (id: number) => faceApi.deleteEvent(id),
    onSuccess: () => { toast.success(t("face.messages.deleteSuccess")); invalidate(); },
    onError,
  });

  const items: FaceEvent[] = data?.items ?? [];
  const total = data?.total ?? 0;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Input className="w-44" placeholder={t("face.events.sourceFilter")}
          value={source} onChange={(e) => { setSource(e.target.value); setOffset(0); }} />
        <label className="flex items-center gap-1.5 text-xs text-muted">
          <Switch checked={unreadOnly}
            onChange={(v) => { setUnreadOnly(v); setOffset(0); }} />
          {t("face.events.unreadOnly")}
        </label>
        <Button variant="ghost" size="sm" onClick={() => refetch()}>
          <RefreshCw size={14} />
        </Button>
        <div className="flex-1" />
        <Button size="sm" variant="secondary" loading={markReadMut.isPending}
          onClick={() => markReadMut.mutate()}>
          <CheckCheck size={14} className="mr-1" />
          {t("face.events.markAllRead")}
        </Button>
      </div>

      {isLoading ? (
        <div className="flex justify-center py-10"><Spinner /></div>
      ) : items.length === 0 ? (
        <EmptyState title={t("face.empty.events")} description={t("face.empty.eventsHint")} />
      ) : (
        <div className="space-y-2">
          {items.map((ev) => (
            <div key={ev.id}
              className={`flex items-start gap-3 rounded-md border px-3 py-2 ${
                ev.read ? "border-border bg-card" : "border-accent/40 bg-elevated"}`}>
              {ev.image_path
                ? <img src={faceApi.imageUrl(ev.image_path)} alt=""
                    onError={onImgError}
                    className="w-16 h-16 rounded object-cover shrink-0 border border-border"
                    loading="lazy" />
                : <div className="w-16 h-16 rounded bg-elevated shrink-0" />}
              <div className="min-w-0 flex-1 space-y-1">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="text-muted">{formatNs(ev.ts_ns)}</span>
                  <Badge variant="neutral">{ev.source || "-"}</Badge>
                  {!ev.read && <Badge variant="ok">{t("face.events.unread")}</Badge>}
                  <span className="text-muted">
                    {ev.faces_count} {t("face.events.faceUnit")}
                  </span>
                </div>
                <div className="flex flex-wrap gap-1">
                  {ev.faces.length === 0
                    ? <span className="text-[11px] text-muted">{t("face.events.noHit")}</span>
                    : ev.faces.map((h, i) => (
                        <Badge key={i}
                          variant={h.matched ? "ok" : h.is_new ? "warn" : "neutral"}>
                          {h.person_name || h.person_key || t("face.events.unknown")}
                          {" "}({h.similarity.toFixed(2)})
                        </Badge>
                      ))}
                </div>
              </div>
              <Button size="sm" variant="ghost" className="shrink-0"
                onClick={() => deleteMut.mutate(ev.id)}>
                <Trash2 size={13} />
              </Button>
            </div>
          ))}
        </div>
      )}

      {total > PAGE && (
        <div className="flex items-center justify-center gap-3 text-xs text-muted">
          <Button size="sm" variant="ghost" disabled={offset === 0}
            onClick={() => setOffset((o) => Math.max(0, o - PAGE))}>
            <ChevronLeft size={14} />
          </Button>
          <span>{offset + 1}–{Math.min(offset + PAGE, total)} / {total}</span>
          <Button size="sm" variant="ghost" disabled={offset + PAGE >= total}
            onClick={() => setOffset((o) => o + PAGE)}>
            <ChevronRight size={14} />
          </Button>
        </div>
      )}
    </div>
  );
}
