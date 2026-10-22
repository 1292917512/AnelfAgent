import { useTranslation } from "react-i18next";
import { useState } from "react";
import { Sparkles } from "lucide-react";
import { Button, Modal } from "@/components/ui";
import { formatSize } from "@/lib/utils";
import type { CogneeGeneration } from "@/lib/types";

export function GenerationCard({ generation, blocker, onRetire, onEnhance, enhanceDisabled }: {
  generation: CogneeGeneration;
  blocker: string;
  onRetire: (generation: CogneeGeneration) => void;
  onEnhance: (name: string, review: boolean) => void;
  enhanceDisabled: boolean;
}) {
  const { t } = useTranslation("memory");
  const [open, setOpen] = useState(false);
  const size = generation.storage;
  return (
    <article aria-label={generation.namespace} className="rounded-lg border border-border bg-elevated p-3 space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <p className="text-sm font-medium text-heading">{t(generation.current ? "cognee.generationCurrent" : "cognee.generationPrevious")}</p>
          <p className="break-all text-xs text-muted">{generation.namespace}</p>
          {generation.current && <p className="text-xs text-accent">{t(`cognee.projectionPhases.${generation.phase}`)}</p>}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" size="sm" onClick={() => setOpen(true)}>{t("cognee.generationView")}</Button>
          {!generation.current && <Button size="sm" disabled={!!blocker} onClick={() => onRetire(generation)}>
            {t("cognee.retireGeneration")}
          </Button>}
        </div>
      </div>
      <div className="space-y-1 text-sm">
        <p>{generation.document_count !== null && generation.processed_count !== null
          ? t("cognee.generationCounts", { count: generation.document_count, processed: generation.processed_count,
            datasets: generation.datasets.length }) : t("cognee.generationCountsUnknown")}</p>
        <p className="text-muted">{size ? t("cognee.datasetBytes", {
          graph: formatSize(size.graph_bytes), vectors: formatSize(size.lance_bytes), total: formatSize(size.total_bytes),
        }) : t("cognee.datasetBytesUnknown")}</p>
        {!generation.current && blocker && <p className="text-xs text-muted">{t(`cognee.cleanupBlockers.${blocker}`)}</p>}
      </div>
      <Modal open={open} onClose={() => setOpen(false)} title={t("cognee.generationView")} width="max-w-2xl"
        footer={!generation.current && <Button variant="danger" disabled={!!blocker} onClick={() => onRetire(generation)}>
          {t("cognee.retireGeneration")}</Button>}>
        <div className="space-y-4 text-sm">
          <p className="break-all font-medium text-heading">{generation.namespace}</p>
          {!generation.current && <p className="text-muted">{t("cognee.generationHistoryUnknown")}</p>}
          <p className="text-muted">{t("cognee.generationMeasurementHint")}</p>
          {!generation.current && <p className="text-muted">{t("cognee.retirementHint")}</p>}
          {blocker && !generation.current && <p className="rounded-md bg-elevated p-3 text-muted">{t(`cognee.cleanupBlockers.${blocker}`)}</p>}
          <p className="font-medium">{t("cognee.generationDetails", { count: generation.datasets.length })}</p>
          {generation.datasets.map(dataset => <div key={dataset.id} className="rounded-md border border-border p-2 space-y-2">
            <p className="break-all text-xs text-heading">{dataset.name}</p>
            <p className="text-xs text-muted">{dataset.document_count === undefined ? t("cognee.generationCountsUnknown")
              : t("cognee.datasetDocuments", { count: dataset.document_count })}</p>
            {generation.current && <div className="flex flex-wrap gap-2">
              {[false, true].map(review => <Button key={String(review)} size="sm" disabled={enhanceDisabled}
                onClick={() => onEnhance(dataset.name, review)}>
                <Sparkles size={14} /> {t(review ? "cognee.reviewConflicts" : "cognee.improve")}
              </Button>)}
            </div>}
          </div>)}
        </div>
      </Modal>
    </article>
  );
}
