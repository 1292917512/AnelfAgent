import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Mic, MicOff, Radio, Zap } from "lucide-react";
import { RealtimeVoiceClient, type RtState } from "@/lib/realtime-voice";
import { useChatStore } from "@/stores/chat-store";
import { configMetaApi } from "@/lib/api";
import { Badge, Button, Switch, toast } from "@/components/ui";

interface TurnEntry {
  role: "user" | "assistant";
  text: string;
}

const STATE_VARIANT: Record<RtState, "ok" | "warn" | "accent"> = {
  listening: "ok",
  thinking: "warn",
  speaking: "accent",
};

/** 实时对话：全双工语音通话（麦克风采集 + 状态条 + 实时转写 + 轮次日志）。 */
export function RealtimePanel() {
  const { t } = useTranslation("sound");
  const [active, setActive] = useState(false);
  const [state, setState] = useState<RtState>("listening");
  const [partial, setPartial] = useState("");
  const [turns, setTurns] = useState<TurnEntry[]>([]);
  const [connecting, setConnecting] = useState(false);
  const clientRef = useRef<RealtimeVoiceClient | null>(null);

  const queryClient = useQueryClient();
  const { data: bargeCfg } = useQuery({
    queryKey: ["configMeta", "realtime_barge_in"],
    queryFn: () => configMetaApi.list().then((r) => r.data),
    select: (data) => {
      for (const g of data.groups) {
        const item = g.items.find((i) => i.key === "realtime_barge_in");
        if (item) return Boolean(item.value);
      }
      return true;
    },
  });

  const bargeMut = useMutation({
    mutationFn: (v: boolean) => configMetaApi.save("realtime_barge_in", v),
    onSuccess: (_r, v) => {
      queryClient.invalidateQueries({ queryKey: ["configMeta", "realtime_barge_in"] });
      toast.success(v ? t("realtime.bargeInOn") : t("realtime.bargeInOff"));
    },
    onError: () => toast.error(t("saveFailed")),
  });

  const stop = () => {
    clientRef.current?.stop();
    clientRef.current = null;
    setActive(false);
    setPartial("");
    setState("listening");
  };

  const start = async () => {
    setConnecting(true);
    const chatId = useChatStore.getState().activeChatId;
    const client = new RealtimeVoiceClient({
      onState: (s) => setState(s),
      onPartial: (text) => setPartial(text),
      onFinal: (text, role) => {
        setPartial("");
        if (text.trim()) {
          setTurns((prev) => [...prev.slice(-19), { role: role === "assistant" ? "assistant" : "user", text }]);
        }
      },
      onError: (msg) => toast.error(msg),
      onClose: () => {
        setActive(false);
        setPartial("");
        setState("listening");
      },
    }, {
      chatId: chatId === "default" ? "" : chatId,
    });
    try {
      await client.start();
      clientRef.current = client;
      setActive(true);
    } catch (err) {
      toast.error(t("realtime.startFailed"));
      console.error(err);
    } finally {
      setConnecting(false);
    }
  };

  useEffect(() => () => stop(), []);

  return (
    <div className="space-y-4 max-w-3xl">
      {/* 控制台 */}
      <div className="rounded-md border border-border bg-card p-4 space-y-3">
        <div className="flex items-center gap-3">
          <Button
            size="sm"
            variant={active ? "danger" : "primary"}
            loading={connecting}
            onClick={active ? stop : start}
          >
            {active ? <MicOff size={14} className="mr-1" /> : <Mic size={14} className="mr-1" />}
            {active ? t("realtime.hangup") : t("realtime.call")}
          </Button>
          <Badge variant={STATE_VARIANT[state]}>
            <Radio size={11} className="mr-1" />
            {t(`realtime.state.${state}`)}
          </Badge>
          {bargeCfg !== undefined && (
            <label className="flex items-center gap-1.5 text-[11px] text-muted">
              <Zap size={11} />
              {t("realtime.bargeIn")}
              <Switch
                checked={bargeCfg}
                onChange={(v) => bargeMut.mutate(v)}
              />
            </label>
          )}
        </div>
        {active && partial && (
          <p className="text-sm text-muted animate-pulse">{partial}</p>
        )}
        {active && !partial && turns.length === 0 && (
          <p className="text-xs text-muted">{t("realtime.hint")}</p>
        )}
      </div>

      {/* 轮次日志 */}
      {turns.length > 0 && (
        <div className="rounded-md border border-border bg-card p-4 space-y-2">
          {turns.map((turn, i) => (
            <div key={i} className="flex gap-2 text-sm">
              <Badge variant={turn.role === "user" ? "accent" : "ok"}>
                {turn.role === "user" ? t("realtime.you") : t("realtime.agent")}
              </Badge>
              <p className="text-foreground">{turn.text}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
