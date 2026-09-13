import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { memoryApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { cn } from "@/lib/utils";
import { Trash2 } from "lucide-react";

const PAGE_SIZE = 200;

interface ConvScope {
  scope_type: string;
  scope_id: string;
  count: number;
  last_ts?: number;
}

interface ConvMessage {
  id?: number;
  role: string;
  content: string;
  ts_ns?: number;
}

function fmtTs(tsNs?: number): string {
  if (!tsNs) return "";
  return new Date(tsNs / 1e6).toLocaleString();
}

function dayStartSeconds(date: string): number {
  return new Date(`${date}T00:00:00`).getTime() / 1000;
}

function dayEndSeconds(date: string): number {
  return new Date(`${date}T23:59:59.999`).getTime() / 1000;
}

export function ConvPanel() {
  const { t } = useTranslation("memory");
  const { t: tc } = useTranslation("common");
  const queryClient = useQueryClient();
  const { data: scopes = [] } = useQuery({ queryKey: ["convScopes"], queryFn: () => memoryApi.conv.scopes().then((r) => r.data) });
  const [selected, setSelected] = useState<{ type: string; id: string } | null>(null);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [messages, setMessages] = useState<ConvMessage[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const reqSeq = useRef(0);
  const listRef = useRef<HTMLDivElement>(null);

  const fetchPage = useCallback(
    async (scope: { type: string; id: string }, from: string, to: string, beforeId?: number) => {
      return memoryApi.conv
        .messages(scope.type, scope.id, {
          limit: PAGE_SIZE,
          ...(beforeId != null ? { beforeId } : {}),
          ...(from ? { tsFrom: dayStartSeconds(from) } : {}),
          ...(to ? { tsTo: dayEndSeconds(to) } : {}),
        })
        .then((r) => r.data as ConvMessage[]);
    },
    [],
  );

  const reload = useCallback(async () => {
    const seq = ++reqSeq.current;
    if (!selected) {
      setMessages([]);
      setHasMore(false);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const page = await fetchPage(selected, dateFrom, dateTo);
      if (seq !== reqSeq.current) return;
      setMessages(page);
      setHasMore(page.length >= PAGE_SIZE);
      requestAnimationFrame(() => {
        const el = listRef.current;
        if (el) el.scrollTop = el.scrollHeight;
      });
    } finally {
      if (seq === reqSeq.current) setLoading(false);
    }
  }, [selected, dateFrom, dateTo, fetchPage]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const loadEarlier = async () => {
    if (!selected || loading || messages.length === 0) return;
    const oldest = messages[0]?.id;
    if (oldest == null) return;
    const seq = reqSeq.current;
    const el = listRef.current;
    const prevHeight = el?.scrollHeight ?? 0;
    const prevTop = el?.scrollTop ?? 0;
    setLoading(true);
    try {
      const page = await fetchPage(selected, dateFrom, dateTo, oldest);
      if (seq !== reqSeq.current) return;
      setMessages((prev) => [...page, ...prev]);
      setHasMore(page.length >= PAGE_SIZE);
      requestAnimationFrame(() => {
        const el2 = listRef.current;
        if (el2) el2.scrollTop = el2.scrollHeight - prevHeight + prevTop;
      });
    } finally {
      if (seq === reqSeq.current) setLoading(false);
    }
  };

  const deleteMsgMutation = useMutation({ mutationFn: (rowId: number) => memoryApi.conv.delete(rowId), onSuccess: () => void reload() });
  const clearConvMutation = useMutation({
    mutationFn: async () => { if (selected) await memoryApi.conv.clear(selected.type, selected.id); },
    onSuccess: () => { void reload(); queryClient.invalidateQueries({ queryKey: ["convScopes"] }); },
  });

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4 md:flex-1 md:min-h-0">
      <Card title={t("conversationList")} className="md:flex md:flex-col md:min-h-0">
        <div className="space-y-1 max-h-[45vh] md:max-h-none overflow-y-auto md:flex-1 md:min-h-0">
          {scopes.length === 0 && <p className="text-sm text-muted">{t("noConversation")}</p>}
          {scopes.map((s: ConvScope) => (
            <button key={`${s.scope_type}-${s.scope_id}`} onClick={() => setSelected({ type: s.scope_type ?? "", id: s.scope_id ?? "" })}
              className={cn("w-full text-left p-2 rounded-md text-sm transition-colors", selected?.id === s.scope_id && selected?.type === s.scope_type ? "bg-accent-subtle text-accent" : "text-foreground hover:bg-hover")}>
              <span className="block truncate">{s.scope_id}<span className="text-xs text-muted ml-1">({s.scope_type})</span></span>
              <span className="block text-xs text-muted mt-0.5">
                {t("messageCount", { count: s.count })}{s.last_ts ? ` · ${fmtTs(s.last_ts)}` : ""}
              </span>
            </button>
          ))}
        </div>
      </Card>
      <Card title={t("messageRecord")} className="md:col-span-2 md:flex md:flex-col md:min-h-0" actions={selected ? (
        <button onClick={() => clearConvMutation.mutate()} className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-danger-subtle text-danger hover:bg-[rgba(239,68,68,0.15)] transition-all"><Trash2 size={14} /> {t("clearConversation")}</button>
      ) : undefined}>
        <div className="flex items-center gap-2 mb-2 text-xs text-muted">
          <label className="flex items-center gap-1">
            {t("dateFrom")}
            <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)}
              className="px-2 py-1 rounded-md border border-border bg-elevated text-foreground text-xs" />
          </label>
          <label className="flex items-center gap-1">
            {t("dateTo")}
            <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)}
              className="px-2 py-1 rounded-md border border-border bg-elevated text-foreground text-xs" />
          </label>
          {(dateFrom || dateTo) && (
            <button onClick={() => { setDateFrom(""); setDateTo(""); }} className="px-2 py-1 rounded-md border border-border text-muted hover:text-foreground hover:bg-hover transition-colors">
              {t("clearDates")}
            </button>
          )}
        </div>
        <div ref={listRef} className="space-y-2 max-h-[45vh] md:max-h-none overflow-y-auto md:flex-1 md:min-h-0">
          {!selected && <p className="text-sm text-muted">{t("selectConversation")}</p>}
          {selected && hasMore && (
            <button onClick={() => void loadEarlier()} disabled={loading}
              className="w-full py-1.5 text-xs text-accent hover:bg-accent-subtle rounded-md transition-colors disabled:opacity-50">
              {loading ? tc("loading") : t("loadEarlier")}
            </button>
          )}
          {selected && !loading && messages.length === 0 && <p className="text-sm text-muted">{t("noMessagesInRange")}</p>}
          {selected && loading && messages.length === 0 && <p className="text-sm text-muted">{tc("loading")}</p>}
          {messages.map((m) => (
            <div key={m.id != null ? String(m.id) : `${m.role}-${m.ts_ns}`} className={cn("flex items-start gap-2 p-2 rounded-md text-sm", m.role === "user" ? "bg-accent-subtle" : "bg-elevated border border-border")}>
              <div className="flex-1 min-w-0">
                <span className="text-xs font-medium text-muted">{m.role}</span>
                {m.ts_ns != null && <span className="text-xs text-muted ml-2">{fmtTs(m.ts_ns)}</span>}
                <p className="text-foreground mt-0.5 break-all">{m.content}</p>
              </div>
              {typeof m.id === "number" && <button onClick={() => deleteMsgMutation.mutate(m.id as number)} className="flex-shrink-0 p-1 text-muted hover:text-danger transition-colors"><Trash2 size={13} /></button>}
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
