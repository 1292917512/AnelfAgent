import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useThinkingSessions } from "@/hooks/useThinking";
import { useNavigate } from "react-router-dom";
import { useThinkingStore } from "@/stores/thinking-store";
import { Loader2 } from "lucide-react";
import { delegationApi } from "@/lib/api";
import { Drawer } from "@/components/common/Drawer";
import { QueryError } from "@/components/common/AsyncState";

/** 进度流 Drawer：轮询尾部日志，自动吸附底部 */
export function ProgressDrawer({
  delegationId,
  title,
  onClose,
}: {
  delegationId: string;
  title: string;
  onClose: () => void;
}) {
  useThinkingSessions();
  const navigate = useNavigate();
  const trace = useThinkingStore((state) => state.sessions.find((session) => session.delegation_id === delegationId));
  const { t } = useTranslation("plan");
  const { data, error, isPending, refetch } = useQuery({
    queryKey: ["delegations", "progress", delegationId],
    queryFn: () => delegationApi.progress(delegationId).then((r) => r.data),
    refetchInterval: (query) => query.state.data?.running === false ? false : 2000,
    throwOnError: false,
  });
  const following = useRef(true);
  const bottomRef = useRef<HTMLDivElement>(null);
  const lineCount = data?.lines.length ?? 0;
  useEffect(() => {
    if (following.current) bottomRef.current?.scrollIntoView({ block: "end" });
  }, [lineCount, delegationId]);

  return (
    <Drawer open onClose={onClose} title={title} width="max-w-xl">
      <div className="flex items-center gap-2 mb-3 text-[11px] text-muted">
        {isPending ? <span>{t("common:loading")}</span> : data?.running ? (
          <span className="inline-flex items-center gap-1 text-accent">
            <Loader2 size={11} className="animate-spin" />
            {t("delegation.running")}
          </span>
        ) : (
          <span>{data ? t("delegation.completed") : ""}</span>
        )}
        {trace && <button className="ml-auto rounded-lg px-2 py-2 text-accent hover:bg-hover" onClick={() => {
          void useThinkingStore.getState().selectSession(trace.id);
          onClose(); navigate("/thinking");
        }}>{t("thinking:openFullTrace")}</button>}
        {data?.truncated && <span>{t("delegation.panel.truncated")}</span>}
      </div>
      {error != null && <QueryError error={error} retry={() => void refetch()} />}
      {lineCount > 0 ? (
        <div onScroll={(event) => { const el = event.currentTarget; following.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48; }} className="max-h-[70dvh] overflow-y-auto rounded-md bg-elevated border border-border p-3 font-mono text-[11px] leading-relaxed text-foreground whitespace-pre-wrap break-words">
          {data?.lines.map((line, i) => <div key={i}>{line}</div>)}
          <div ref={bottomRef} />
        </div>
      ) : !isPending && !error && (
        <p className="text-muted text-sm py-6 text-center">{t("delegation.panel.progressEmpty")}</p>
      )}
    </Drawer>
  );
}
