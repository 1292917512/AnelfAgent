import { createContext, useContext, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Headphones, Loader2, Mic, PhoneOff, Volume2 } from "lucide-react";
import { RealtimeVoiceClient, type CallMode, type RtState } from "@/lib/realtime-voice";
import { useChatStore } from "@/stores/chat-store";
import { toast } from "@/components/ui";

const STATE_LABEL: Record<RtState, string> = {
  listening: "chat.call.stateListening",
  thinking: "chat.call.stateThinking",
  speaking: "chat.call.stateSpeaking",
};

const STATE_COLOR: Record<RtState, string> = {
  listening: "bg-green-500",
  thinking: "bg-yellow-500",
  speaking: "bg-accent",
};

const MODE_STORAGE_KEY = "realtime_call_mode";

interface CallState {
  active: boolean;
  connecting: boolean;
  state: RtState;
  /** 语音收束进入定稿（转写进行中）：收听中状态下展示「转写中」 */
  finalizing: boolean;
  callMode: CallMode;
  pttPressed: boolean;
  partial: string;
  level: number;
  volume: number;
  start: () => Promise<void>;
  stop: () => void;
  setVolume: (v: number) => void;
  setCallMode: (m: CallMode) => void;
  pressPtt: () => void;
  releasePtt: () => void;
}

const CallCtx = createContext<CallState | null>(null);
const useCall = () => useContext(CallCtx);

/** 工作区实时通话状态源：Toggle（输入框旁按钮）与 Panel（通话状态条）共享。 */
export function RealtimeCallProvider({ children }: { children: React.ReactNode }) {
  const { t } = useTranslation("chat");
  const [active, setActive] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [state, setState] = useState<RtState>("listening");
  const [finalizing, setFinalizing] = useState(false);
  const [callMode, setCallModeState] = useState<CallMode>(() => {
    const saved = localStorage.getItem(MODE_STORAGE_KEY);
    return saved === "ptt" ? "ptt" : "free";
  });
  const [pttPressed, setPttPressed] = useState(false);
  const [partial, setPartial] = useState("");
  const [level, setLevel] = useState(0);
  const [volume, setVolume] = useState(1);
  const clientRef = useRef<RealtimeVoiceClient | null>(null);
  const chatId = useChatStore((s) => s.activeChatId);

  const stop = () => {
    clientRef.current?.stop();
    clientRef.current = null;
    setActive(false);
    setPartial("");
    setState("listening");
    setFinalizing(false);
    setPttPressed(false);
    setLevel(0);
  };

  const setCallMode = (m: CallMode) => {
    setCallModeState(m);
    localStorage.setItem(MODE_STORAGE_KEY, m);
    clientRef.current?.setCallMode(m);
  };

  const pressPtt = () => {
    setPttPressed(true);
    clientRef.current?.pttDown();
  };

  const releasePtt = () => {
    setPttPressed(false);
    clientRef.current?.pttUp();
  };

  const start = async () => {
    setConnecting(true);
    const client = new RealtimeVoiceClient({
      onState: (s) => {
        setState(s);
        if (s !== "listening") setFinalizing(false);
      },
      onLevel: (l) => setLevel(l),
      onPartial: (text) => setPartial(text),
      onFinalizing: () => setFinalizing(true),
      onFinal: () => {
        setPartial("");
        setFinalizing(false);
      },
      onError: (msg, lvl) => {
        setFinalizing(false);
        if (lvl === "info") toast.info(msg);
        else toast.error(msg);
      },
      onClose: () => {
        setActive(false);
        setPartial("");
        setFinalizing(false);
        setPttPressed(false);
        setLevel(0);
      },
    }, {
      chatId: chatId === "default" ? "" : chatId,
      callMode,
    });
    try {
      await client.start();
      clientRef.current = client;
      client.setOutputVolume(volume);
      setActive(true);
    } catch (err) {
      const e = err as { name?: string; message?: string };
      let msg = t("call.startFailed");
      if (e?.name === "NotAllowedError") msg = t("call.micDenied");
      else if (e?.name === "NotFoundError") msg = t("call.micNotFound");
      else if (e?.name === "NotReadableError") msg = t("call.micBusy");
      else if (!navigator.mediaDevices?.getUserMedia) msg = t("call.micInsecure");
      else if (e?.name === "Error" && e.message && e.message !== "Failed to fetch") msg = e.message;
      toast.error(msg);
    } finally {
      setConnecting(false);
    }
  };

  useEffect(() => () => stop(), []);

  return (
    <CallCtx.Provider value={{
      active, connecting, state, finalizing, callMode, pttPressed, partial,
      level, volume, start, stop,
      setVolume: (v: number) => {
        setVolume(v);
        clientRef.current?.setOutputVolume(v);
      },
      setCallMode, pressPtt, releasePtt,
    }}>
      {children}
    </CallCtx.Provider>
  );
}

/** 通话开关按钮（输入框工具行，回形针旁）。 */
export function RealtimeCallToggle() {
  const { t } = useTranslation("chat");
  const call = useCall();
  if (!call) return null;
  return (
    <button
      onClick={() => void (call.active ? call.stop() : call.start())}
      disabled={call.connecting}
      className={`flex items-center justify-center w-9 h-9 rounded-md transition-colors disabled:opacity-60 ${
        call.active ? "text-accent" : "text-muted hover:text-accent hover:bg-hover"}`}
      title={call.active ? t("call.hangup") : t("call.startTitle")}
    >
      {call.connecting ? <Loader2 size={18} className="animate-spin" /> : <Headphones size={18} />}
    </button>
  );
}

/** 通话状态条（仅通话中渲染于输入卡上方）：状态/转写/电平/模式/音量/挂断。 */
export function RealtimeCallPanel() {
  const { t } = useTranslation("chat");
  const call = useCall();
  if (!call || !call.active) return null;
  const statusLabel = call.finalizing && call.state === "listening"
    ? t("call.stateFinalizing")
    : call.state === "listening" && call.callMode === "ptt" && !call.pttPressed
      ? t("call.stateStandby")
      : t(STATE_LABEL[call.state]);
  return (
    <div className="mb-2 rounded-lg border border-border bg-elevated px-3 py-2 space-y-1.5">
      <div className="flex items-center gap-2 flex-wrap">
        <span className={`w-2 h-2 rounded-full animate-pulse ${STATE_COLOR[call.state]}`} />
        <span className="text-xs font-medium text-foreground">{statusLabel}</span>
        <div className="flex items-center gap-[2px] h-3" title={t("call.micLevel")}>
          {[0.15, 0.35, 0.55, 0.75, 0.92].map((th) => (
            <span
              key={th}
              className={`w-[3px] rounded-sm transition-colors ${
                call.level >= th ? "bg-green-500" : "bg-border"}`}
              style={{ height: `${4 + th * 8}px` }}
            />
          ))}
        </div>
        {call.partial && (
          <span className="text-xs text-muted truncate max-w-[40%] animate-pulse">{call.partial}</span>
        )}
        <div className="ml-auto flex items-center gap-2">
          <div className="flex items-center rounded-md border border-border overflow-hidden" role="group">
            {(["free", "ptt"] as const).map((m) => (
              <button
                key={m}
                onClick={() => call.setCallMode(m)}
                className={`px-2 py-0.5 text-[11px] transition-colors ${
                  call.callMode === m
                    ? "bg-accent text-white"
                    : "text-muted hover:text-accent hover:bg-hover"}`}
              >
                {t(m === "free" ? "call.modeFree" : "call.modePtt")}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-1" title={t("call.volume")}>
            <Volume2 size={13} className="text-muted" />
            <input
              type="range" min={0} max={1} step={0.05} value={call.volume}
              onChange={(e) => call.setVolume(Number(e.target.value))}
              className="w-16 h-1 accent-[var(--accent)]"
            />
          </div>
          <button
            onClick={call.stop}
            className="flex items-center gap-1 text-xs px-2 py-1 rounded-md bg-danger text-white hover:opacity-90 transition-opacity"
          >
            <PhoneOff size={12} />
            {t("call.hangup")}
          </button>
        </div>
      </div>
      {call.callMode === "ptt" && (
        <button
          onPointerDown={(e) => {
            e.preventDefault();
            call.pressPtt();
          }}
          onPointerUp={call.releasePtt}
          onPointerLeave={call.releasePtt}
          onPointerCancel={call.releasePtt}
          onContextMenu={(e) => e.preventDefault()}
          className={`w-full flex items-center justify-center gap-2 py-2 rounded-md text-sm
                     font-medium transition-colors touch-none select-none ${
                       call.pttPressed
                         ? "bg-accent text-white"
                         : "bg-hover text-foreground hover:bg-accent/80 hover:text-white"}`}
        >
          <Mic size={15} />
          {call.pttPressed ? t("call.pttRelease") : t("call.pttHold")}
        </button>
      )}
      <div className="flex items-center gap-1 text-[10px] text-muted">
        <Mic size={10} />
        {t(call.callMode === "ptt" ? "call.hintPtt" : "call.hint")}
      </div>
    </div>
  );
}
