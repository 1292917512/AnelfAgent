import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router-dom";
import { runtimeStatusQuery } from "@/lib/runtime-queries";
import { cn } from "@/lib/utils";

export function ConnectionStatus() {
  const { t } = useTranslation("common");
  const { pathname } = useLocation();
  const status = useQuery({
    ...runtimeStatusQuery(),
    // 全局唯一状态轮询；页面面板订阅同一查询缓存，重连仍可主动失效刷新。
    refetchInterval: (query) => {
      if (pathname === "/dashboard") return query.state.data?.status?.mind_phase === "idle" ? 10_000 : 3000;
      return pathname === "/" || pathname === "/heartbeat" ? 5000 : 15_000;
    },
  });
  const label = status.isPending ? t("connecting") : status.isError ? t("serviceUnavailable") : status.data?.ready ? t("serviceReady") : t("notReady");
  return (
    <button type="button" onClick={() => void status.refetch()} title={label} aria-label={label}
      className="hidden items-center gap-1.5 rounded-md px-2 py-1 text-[11px] text-muted sm:flex">
      <span className={cn("h-1.5 w-1.5 rounded-full", status.isPending ? "bg-warn animate-pulse" : status.isError ? "bg-danger" : status.data?.ready ? "bg-ok" : "bg-warn")} />
      <span className="hidden xl:inline">{label}</span>
    </button>
  );
}
