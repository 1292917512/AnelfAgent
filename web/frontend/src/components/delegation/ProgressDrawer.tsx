import { useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useThinkingSessions } from "@/hooks/useThinking";
import { useNavigate } from "react-router-dom";
import { useThinkingStore } from "@/stores/thinking-store";
import { Loader2 } from "lucide-react";
import { delegationApi } from "@/lib/api";
import { Drawer } from "@/components/common/Drawer";
import { QueryError } from "@/components/common/AsyncState";
import type { DelegationHistoryItem, DelegationOverviewItem } from "@/lib/types";
import { formatDuration, formatTokens } from "./format";

/** 进度流 Drawer：轮询尾部日志，自动吸附底部 */
export function ProgressDrawer({
  delegationId,
  title,
  onClose,
  item,
}: {
  delegationId: string;
  title: string;
  onClose: () => void;
  item?: DelegationHistoryItem | DelegationOverviewItem;
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
  const scrollRef = useRef<HTMLDivElement>(null);
  const lineCount = data?.lines.length ?? 0;
  useEffect(() => {
    const element = scrollRef.current;
    if (following.current && data?.running && element) element.scrollTop = element.scrollHeight;
  }, [data?.lines, data?.running, delegationId]);

  return (
    <Drawer open onClose={onClose} title={t("dashboard:history.executionDetail")} width="max-w-xl">
      <h3 className="text-sm leading-relaxed break-words font-medium text-heading mb-4">{title}</h3>
      <div className="flex items-center gap-2 mb-3 text-[11px] text-muted">
        {isPending ? <span>{t("common:loading")}</span> : data?.running ? (
          <span className="inline-flex items-center gap-1 text-accent">
            <Loader2 size={11} className="animate-spin" />
            {t("delegation.running")}
          </span>
        ) : (
          <span>{data ? item && "status" in item ? t(`delegation.panel.status${item.status.charAt(0).toUpperCase()}${item.status.slice(1)}`, { defaultValue: item.status }) : t("dashboard:history.ended") : ""}</span>
        )}
        {trace && <button className="ml-auto rounded-lg px-2 py-2 text-accent hover:bg-hover" onClick={() => {
          void useThinkingStore.getState().selectSession(trace.id);
          onClose(); navigate("/thinking");
        }}>{t("thinking:openFullTrace")}</button>}
        {data?.truncated && <span>{t("delegation.panel.truncated")}</span>}
      </div>
      {item && <div className="delegation-detail-meta">
        <dl>
          <div><dt>{t("dashboard:history.executionId")}</dt><dd>{item.delegation_id}</dd></div>
          {item.model && <div><dt>{t("dashboard:history.model")}</dt><dd>{item.model}</dd></div>}
          {item.agent && <div><dt>{t("dashboard:history.agent")}</dt><dd>{item.agent}</dd></div>}
          <div><dt>{t("dashboard:history.scope")}</dt><dd>{item.scope || "—"}</dd></div>
          {"duration_seconds" in item && <div><dt>{t("dashboard:history.duration")}</dt><dd>{formatDuration(item.duration_seconds)}</dd></div>}
          {item.parent_id && <div><dt>{t("dashboard:history.parent")}</dt><dd>{item.parent_id}</dd></div>}
          {"resumed_from" in item && item.resumed_from && <div><dt>{t("dashboard:history.resumedFrom")}</dt><dd>{item.resumed_from}</dd></div>}
          {(item.usage?.turns ?? 0) > 0 && <div><dt>{t("dashboard:history.usage")}</dt><dd>{t("delegation.panel.turns", { n: item.usage?.turns })} · {formatTokens((item.usage?.input_tokens ?? 0) + (item.usage?.output_tokens ?? 0))} tokens</dd></div>}
        </dl>
        {"summary" in item && item.summary && <div className="delegation-summary-markdown">
          <h4>{t("dashboard:history.resultSummary")}</h4>
          <ReactMarkdown skipHtml components={{ img: ({ alt }) => <span>{alt}</span> }}>{item.summary}</ReactMarkdown>
        </div>}
      </div>}
      {error != null && <QueryError error={error} retry={() => void refetch()} />}
      {lineCount > 0 ? (
        <div ref={scrollRef} onScroll={(event) => { const el = event.currentTarget; following.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48; }} className="delegation-progress-log">
          {data?.lines.map((line, i) => {
            const match = line.match(/^\[(\d{2}:\d{2}:\d{2})\]\s?(.*)$/);
            return <div key={i}><time>{match?.[1]}</time><span>{match?.[2] ?? line}</span></div>;
          })}
        </div>
      ) : !isPending && !error && (
        <p className="text-muted text-sm py-6 text-center">{t("delegation.panel.progressEmpty")}</p>
      )}
    </Drawer>
  );
}
