import { useTranslation } from "react-i18next";
import type { CogneeResolvedInfo } from "@/lib/types";

export function ResolvedModelLine({ label, info }: { label: string; info?: CogneeResolvedInfo }) {
  const { t } = useTranslation("memory");
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span className="text-muted w-20 flex-shrink-0">{label}</span>
      {info?.model ? <>
        <span className="min-w-0 break-all font-mono text-heading">{info.model}</span>
        {info.instructor_mode && (
          <span className="px-1.5 py-0.5 rounded bg-elevated border border-border text-muted">
            {info.instructor_mode}
          </span>
        )}
        {info.request_timeout_seconds !== undefined && <span>{t("cognee.requestTimeoutActive", { seconds: info.request_timeout_seconds })}</span>}
        {info.endpoint && <span className="text-muted truncate max-w-64">{info.endpoint}</span>}
      </> : <span className="text-muted">{t("cognee.notResolved")}</span>}
    </div>
  );
}
