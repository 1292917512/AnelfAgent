import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { QueryError } from "@/components/common/AsyncState";
import { useChatStore } from "@/stores/chat-store";

/** Shows conversation loading failures without hiding the messages already available. */
export function HistoryStatus() {
  const { t } = useTranslation("chat");
  const id = useChatStore((state) => state.activeChatId);
  const loaded = useChatStore((state) => state.buckets[id]?.historyLoaded);
  const loading = useChatStore((state) => state.buckets[id]?.historyLoading);
  const error = useChatStore((state) => state.buckets[id]?.historyError);
  const earlierError = useChatStore((state) => state.buckets[id]?.earlierError);
  const load = useChatStore((state) => state.loadHistory);
  const refresh = useChatStore((state) => state.refreshAfterReconnect);
  const earlier = useChatStore((state) => state.loadEarlier);
  return <>{loading && <p role="status" className="flex items-center justify-center gap-2 py-2 text-xs text-muted">
    <Loader2 size={13} className="animate-spin" />{t("historyLoading")}
  </p>}
    {error != null && <QueryError compact error={error} retry={() => void (loaded ? refresh(id) : load(id))} />}
    {earlierError != null && <QueryError compact error={earlierError} retry={() => void earlier(id)} />}
  </>;
}
