import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { statusApi } from "@/lib/api";
import { cn } from "@/lib/utils";

export function ConnectionStatus() {
  const { t } = useTranslation("common");
  const status = useQuery({
    queryKey: ["status"], queryFn: () => statusApi.get().then((response) => response.data),
    refetchInterval: 15_000, retry: false, throwOnError: false,
  });
  const label = status.isPending ? t("connecting") : status.isError ? t("disconnected") : status.data?.ready ? t("connected") : t("notReady");
  return (
    <button type="button" onClick={() => void status.refetch()} title={label} aria-label={label}
      className="hidden items-center gap-1.5 rounded-md px-2 py-1 text-[11px] text-muted sm:flex">
      <span className={cn("h-1.5 w-1.5 rounded-full", status.isPending ? "bg-warn animate-pulse" : status.isError ? "bg-danger" : status.data?.ready ? "bg-ok" : "bg-warn")} />
      <span className="hidden xl:inline">{label}</span>
    </button>
  );
}
