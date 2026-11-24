import { useSyncExternalStore } from "react";
import * as Popover from "@radix-ui/react-popover";
import { Radio, RefreshCw, WifiOff } from "lucide-react";
import { PopoverContent } from "@/components/ui/PopoverContent";
import { Button } from "@/components/ui/Button";
import { useChatStore } from "@/stores/chat-store";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router-dom";
import { runtimeStatusQuery } from "@/lib/runtime-queries";
import { cn } from "@/lib/utils";

function subscribeNetwork(notify: () => void) {
  window.addEventListener("online", notify);
  window.addEventListener("offline", notify);
  return () => {
    window.removeEventListener("online", notify);
    window.removeEventListener("offline", notify);
  };
}

export function ConnectionStatus() {
  const { t } = useTranslation("common");
  const online = useSyncExternalStore(subscribeNetwork, () => navigator.onLine);
  const stream = useChatStore((state) => state.streamStatus);
  const { pathname } = useLocation();
  const status = useQuery({
    ...runtimeStatusQuery(),
    refetchOnWindowFocus: "always",
    refetchOnReconnect: "always",
    // 全局唯一状态轮询；页面面板订阅同一查询缓存，重连仍可主动失效刷新。
    refetchInterval: (query) => {
      if (pathname === "/dashboard") return query.state.data?.status?.mind_phase === "idle" ? 10_000 : 3000;
      return pathname === "/" || pathname === "/heartbeat" ? 5000 : 15_000;
    },
  });
  const http = !online ? "offline" : status.isError ? "unavailable" : status.data ? "reachable" : "connecting";
  const runtime = http === "reachable" ? (status.data?.ready ? "ready" : "starting") : "unknown";
  const live = online ? stream : "offline";
  const summary = http !== "reachable" ? http : runtime !== "ready" ? runtime : live;
  const healthy = summary === "connected";
  const Icon = healthy ? Radio : summary === "offline" || summary === "unavailable" ? WifiOff : RefreshCw;
  const title = t("connection.title");
  const label = t(`connection.${summary}`);
  return <Popover.Root>
    <span aria-live="polite" aria-atomic="true" className="sr-only">{title} · {label}</span>
    <Popover.Trigger asChild>
      <button type="button" aria-label={`${title} · ${label}`} title={label}
        className={cn("flex min-h-8 max-w-28 items-center gap-1 rounded-md px-1.5 text-xs hover:bg-hover pointer-coarse:min-h-11",
          healthy ? "text-ok" : http === "offline" || http === "unavailable" ? "text-danger" : "text-warn")}>
        <Icon size={14} className="shrink-0" /><span className="truncate">{label}</span>
      </button>
    </Popover.Trigger>
    <PopoverContent align="end" aria-label={title} className="w-72 space-y-3 p-3">
      <p className="text-sm font-medium text-heading">{title}</p>
      <dl className="space-y-1.5 text-xs">
        {Object.entries({ http, runtime, live }).map(([key, value]) =>
          <div key={key} className="flex items-baseline justify-between gap-3">
            <dt className="text-muted">{t(`connection.${key}`)}</dt><dd>{t(`connection.${value}`)}</dd>
          </div>)}
        {status.dataUpdatedAt > 0 && <div className="flex items-baseline justify-between gap-3">
          <dt className="text-muted">{t("connection.lastSample")}</dt>
          <dd>{new Date(status.dataUpdatedAt).toLocaleTimeString(document.documentElement.lang)}</dd>
        </div>}
      </dl>
      <Button size="sm" disabled={!online} onClick={() => {
        void status.refetch();
        if (stream !== "connected") useChatStore.getState().refreshRealtime();
      }}><RefreshCw size={13} />{t("connection.check")}</Button>
    </PopoverContent>
  </Popover.Root>;
}
