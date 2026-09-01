import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiErrorMessage } from "@/lib/api";
import { acfunApi } from "../api";
import { cn } from "@/lib/utils";
import type { AcfunLiveRoom } from "../types";
import {
  Radio, Plus, X, Loader2, Wifi, WifiOff, RefreshCw, CircleOff, Eye, ThumbsUp, Citrus,
} from "lucide-react";

const STATE_STYLE: Record<string, { cls: string; icon: typeof Wifi }> = {
  connected: { cls: "bg-ok-subtle text-ok border-[rgba(34,197,94,0.3)]", icon: Wifi },
  connecting: { cls: "bg-warn-subtle text-warn border-[rgba(245,158,11,0.3)]", icon: Loader2 },
  reconnecting: { cls: "bg-warn-subtle text-warn border-[rgba(245,158,11,0.3)]", icon: RefreshCw },
  closed: { cls: "bg-secondary text-muted border-border", icon: CircleOff },
  stopped: { cls: "bg-secondary text-muted border-border", icon: WifiOff },
  disconnected: { cls: "bg-secondary text-muted border-border", icon: WifiOff },
};

const DEFAULT_STATE_STYLE = STATE_STYLE.disconnected as { cls: string; icon: typeof Wifi };

function formatUptime(seconds: number): string {
  const s = Math.floor(seconds || 0);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}min`;
  return `${Math.floor(s / 3600)}h${Math.floor((s % 3600) / 60)}min`;
}

function formatAge(age: number | null | undefined): string {
  if (age === null || age === undefined) return "-";
  if (age < 5) return "<5s";
  return formatUptime(age);
}

/** 单个直播房间卡片：状态徽标 + 标题/UP + 实时计数 + 弹幕流 + 礼物 + 诊断 */
function RoomCard({ room }: { room: AcfunLiveRoom }) {
  const { t } = useTranslation("channel-acfun");
  const style = STATE_STYLE[room.state] ?? DEFAULT_STATE_STYLE;
  const Icon = style.icon;
  return (
    <div className="rounded-md border border-border bg-secondary/40 p-3 space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <span className={cn(
          "flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full border font-medium",
          style.cls,
        )}>
          <Icon size={11} className={room.state === "connecting" || room.state === "reconnecting" ? "animate-spin" : ""} />
          {t(`live.state.${room.state}`, { defaultValue: room.state })}
        </span>
        <span className="text-xs font-medium text-heading truncate">
          {room.title || `live:${room.uid}`}
        </span>
        {room.user_name && <span className="text-[11px] text-muted">@{room.user_name}</span>}
        {room.state === "connected" && room.uptime > 0 && (
          <span className="text-[10px] text-muted font-mono ml-auto">
            {t("live.uptime")} {formatUptime(room.uptime)}
          </span>
        )}
      </div>

      {room.detail && room.state !== "connected" && (
        <p className="text-[11px] text-muted">{room.detail}</p>
      )}

      {room.state === "connected" && (
        <div className="flex items-center gap-4 text-[11px] text-muted">
          <span className="flex items-center gap-1"><Eye size={11} /> {room.watching || "-"}</span>
          <span className="flex items-center gap-1"><ThumbsUp size={11} /> {room.likes || "-"}</span>
          <span className="flex items-center gap-1"><Citrus size={11} /> {room.banana || "-"}</span>
          <span className="ml-auto">{t("live.danmaku5m", { count: room.danmaku_recent })}</span>
        </div>
      )}

      {room.recent_danmaku.length > 0 && (
        <div className="space-y-0.5 max-h-[140px] overflow-y-auto">
          {room.recent_danmaku.map((d, i) => (
            <p key={i} className="text-[11px] text-foreground/80 truncate">
              <span className="text-muted">[{d.name || d.uid}]</span> {d.text}
            </p>
          ))}
        </div>
      )}

      {room.recent_gifts.length > 0 && (
        <p className="text-[11px] text-warn truncate">
          🎁 {room.recent_gifts.join("；")}
        </p>
      )}

      <p className="text-[10px] text-muted font-mono">
        {t("live.diag")}: 💬{room.stats.danmaku} 👍{room.stats.likes} 🎁{room.stats.gifts}
        {" · "}{t("live.reconnects")} {room.stats.reconnects}
        {" · "}{t("live.lastSignal")} {formatAge(room.stats.last_signal_age)}
        {room.stats.last_error && (
          <span className="text-danger"> · {t("live.lastError")}: {room.stats.last_error}</span>
        )}
      </p>
    </div>
  );
}

/**
 * AcFun 直播模式面板 — 模式开关 + 观察房间管理 + 实时房间状态（3s 轮询）。
 * 挂在频道卡片展开区，与 AI 的 acfun_live_* 工具同源（同后端端点/同持久化）。
 */
export default function AcfunLivePanel() {
  const { t } = useTranslation("channel-acfun");
  const queryClient = useQueryClient();
  const [watchInput, setWatchInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const { data } = useQuery({
    queryKey: ["acfunLiveStatus"],
    queryFn: async () => (await acfunApi.liveStatus()).data,
    refetchInterval: 3000,
    refetchIntervalInBackground: false,
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["acfunLiveStatus"] });

  const run = async (fn: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const resp = (await fn()) as { data?: { success?: boolean; error_msg?: string } };
      if (resp.data && resp.data.success === false) {
        setError(resp.data.error_msg ?? t("live.opFailed"));
      }
      refresh();
      queryClient.invalidateQueries({ queryKey: ["adapters"] });
      queryClient.invalidateQueries({ queryKey: ["adapterConfigs"] });
    } catch (e) {
      setError(apiErrorMessage(e, t("live.opFailed")));
    } finally {
      setBusy(false);
    }
  };

  const toggleMode = () => run(() => acfunApi.liveMode(!(data?.mode ?? false)));
  const addWatch = () => {
    const uid = watchInput.trim();
    if (!uid) return;
    setWatchInput("");
    run(() => acfunApi.liveWatch(uid));
  };
  const removeWatch = (uid: string) => run(() => acfunApi.liveUnwatch(uid));

  const mode = data?.mode ?? false;
  const watched = data?.watched ?? [];
  const rooms = data?.rooms ?? [];

  return (
    <div className="rounded-md border border-border p-3 space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold text-muted uppercase tracking-wider flex items-center gap-1.5">
          <Radio size={13} className={mode ? "text-danger" : "text-muted"} />
          {t("live.title")}
          {mode && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-danger-subtle text-danger border border-[rgba(239,68,68,0.3)] normal-case">
              LIVE
            </span>
          )}
        </p>
        <button
          onClick={toggleMode}
          disabled={busy}
          className={cn(
            "flex items-center gap-1.5 px-3 py-1 text-xs font-medium rounded-md border transition-all disabled:opacity-70",
            mode
              ? "border-[rgba(239,68,68,0.3)] text-danger hover:bg-danger-subtle"
              : "border-[rgba(34,197,94,0.3)] text-ok hover:bg-ok-subtle",
          )}
        >
          {busy && <Loader2 size={12} className="animate-spin" />}
          {mode ? t("live.modeOff") : t("live.modeOn")}
        </button>
      </div>

      {/* 观察列表管理 */}
      <div className="flex items-center gap-2">
        <input
          value={watchInput}
          onChange={(e) => setWatchInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && addWatch()}
          placeholder={t("live.watchPlaceholder")}
          className="flex-1 px-2.5 py-1.5 text-xs rounded-md border border-border bg-secondary text-foreground placeholder:text-muted focus:outline-none focus:border-accent"
        />
        <button
          onClick={addWatch}
          disabled={busy || !watchInput.trim()}
          className="flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium rounded-md border border-border text-muted hover:text-foreground hover:bg-hover transition-all disabled:opacity-50"
        >
          <Plus size={12} /> {t("live.watch")}
        </button>
      </div>

      {watched.length > 0 && (
        <div className="flex items-center gap-1.5 flex-wrap">
          {watched.map((uid) => (
            <span key={uid} className="flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-secondary border border-border text-muted">
              live:{uid}
              <button onClick={() => removeWatch(uid)} className="hover:text-danger transition-colors">
                <X size={10} />
              </button>
            </span>
          ))}
        </div>
      )}

      {error && <p className="text-[11px] text-danger">{error}</p>}

      {/* 实时房间状态 */}
      {mode && rooms.length > 0 && (
        <div className="space-y-2">
          {rooms.map((room) => <RoomCard key={room.uid} room={room} />)}
        </div>
      )}
      {mode && rooms.length === 0 && (
        <p className="text-[11px] text-muted text-center py-2">{t("live.noRooms")}</p>
      )}
      {!mode && (
        <p className="text-[11px] text-muted">{t("live.modeOffHint")}</p>
      )}
    </div>
  );
}
