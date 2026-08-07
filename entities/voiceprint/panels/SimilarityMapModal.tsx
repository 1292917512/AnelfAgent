import { Fragment } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { voiceprintApi } from "@/lib/api";
import { Badge, Modal, Spinner } from "@/components/ui";

interface SimilarityMapModalProps {
  open: boolean;
  onClose: () => void;
}

/** 相似度分布热力图：说话人 × 说话人矩阵（行序已按簇分组，成块即同一人分裂）。 */
export function SimilarityMapModal({ open, onClose }: SimilarityMapModalProps) {
  const { t } = useTranslation("voiceprint");

  const { data, isLoading } = useQuery({
    queryKey: ["voiceprintSimilarityMap"],
    queryFn: () => voiceprintApi.similarityMap({ status: "pending" }).then((r) => r.data),
    enabled: open,
  });

  const matrix = data?.matrix;
  const speakerById = new Map((data?.speakers ?? []).map((s) => [s.id, s]));

  const cellColor = (sim: number, diagonal: boolean) => {
    if (diagonal) return "transparent";
    if (sim >= 0.85) return "rgba(34, 197, 94, 0.9)";
    if (sim >= 0.70) return "rgba(34, 197, 94, 0.55)";
    if (sim >= 0.55) return "rgba(245, 158, 11, 0.45)";
    if (sim >= 0.40) return "rgba(245, 158, 11, 0.18)";
    return "transparent";
  };

  return (
    <Modal open={open} onClose={onClose} title={t("simmap.title")} width="max-w-3xl">
      {isLoading ? (
        <div className="flex justify-center py-10"><Spinner /></div>
      ) : !data || data.speakers.length === 0 ? (
        <p className="text-sm text-muted py-2">{t("simmap.empty")}</p>
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
            <Badge variant="accent">
              {t("simmap.estimated", { count: data.estimated_persons })}
            </Badge>
            <Badge variant="neutral">
              {t("simmap.total", { count: data.speakers_total })}
            </Badge>
            <span>{t("simmap.thresholdHint", { threshold: data.threshold })}</span>
          </div>

          {matrix && (
            <div className="overflow-x-auto">
              <div
                className="inline-grid gap-px"
                style={{
                  gridTemplateColumns: `minmax(90px, auto) repeat(${matrix.order.length}, 18px)`,
                }}
              >
                <div />
                {matrix.order.map((id) => (
                  <div
                    key={`col-${id}`}
                    className="text-[9px] text-muted text-center truncate"
                    title={speakerById.get(id)?.speaker_key}
                  >
                    {speakerById.get(id)?.name?.slice(0, 2)
                      || speakerById.get(id)?.speaker_key.slice(-2)}
                  </div>
                ))}
                {matrix.order.map((rowId, ri) => (
                  <Fragment key={`row-${rowId}`}>
                    <div
                      className="text-[10px] text-muted truncate pr-1 flex items-center"
                      title={speakerById.get(rowId)?.speaker_key}
                    >
                      {speakerById.get(rowId)?.name
                        || speakerById.get(rowId)?.speaker_key}
                    </div>
                    {matrix.values[ri]!.map((sim, ci) => (
                      <div
                        key={`c-${rowId}-${matrix.order[ci]}`}
                        className="w-[18px] h-[18px] rounded-[2px] border border-border/40"
                        style={{ backgroundColor: cellColor(sim, ri === ci) }}
                        title={`${speakerById.get(rowId)?.name || speakerById.get(rowId)?.speaker_key} ↔ ${speakerById.get(matrix.order[ci]!)?.name || speakerById.get(matrix.order[ci]!)?.speaker_key}: ${sim.toFixed(3)}`}
                      />
                    ))}
                  </Fragment>
                ))}
              </div>
            </div>
          )}

          <div className="flex items-center gap-3 text-[10px] text-muted">
            <span className="flex items-center gap-1">
              <i className="inline-block w-3 h-3 rounded-sm" style={{ backgroundColor: "rgba(34,197,94,0.9)" }} />
              ≥0.85
            </span>
            <span className="flex items-center gap-1">
              <i className="inline-block w-3 h-3 rounded-sm" style={{ backgroundColor: "rgba(34,197,94,0.55)" }} />
              ≥0.70 可合并
            </span>
            <span className="flex items-center gap-1">
              <i className="inline-block w-3 h-3 rounded-sm" style={{ backgroundColor: "rgba(245,158,11,0.45)" }} />
              ≥0.55
            </span>
          </div>

          {/* 聚邻列表（与热力图行序一致，含可合并标记） */}
          <div className="max-h-56 overflow-y-auto space-y-1">
            {data.speakers.map((s) => (
              <div key={s.id} className="flex items-center gap-2 text-xs">
                <span className="text-foreground whitespace-nowrap">
                  {s.name || s.speaker_key}
                </span>
                <span className="text-muted">↔</span>
                <div className="flex flex-wrap gap-1">
                  {s.top_similar.length === 0 ? (
                    <span className="text-muted">{t("simmap.noSimilar")}</span>
                  ) : (
                    s.top_similar.map((n) => (
                      <Badge key={n.id} variant={n.mergable ? "ok" : "neutral"}>
                        {n.name || n.speaker_key} {n.similarity.toFixed(2)}
                      </Badge>
                    ))
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </Modal>
  );
}
