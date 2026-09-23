/** 拖拽诊断角标（临时）：把拖出/落下的关键事件直接画到页面角落，便于用户反馈现场。
 * 排查完成后可整体移除。
 */

import { useEffect, useState } from "react";
import { consumeWorkspaceDragPayload } from "./workspace-drag";

interface DragDiagState {
  lastDragStart: string;
  lastDrop: string;
  payloadNow: string;
}

export function DragDiagBadge() {
  const [state, setState] = useState<DragDiagState>({ lastDragStart: "", lastDrop: "", payloadNow: "" });

  useEffect(() => {
    const onDragStart = (e: Event) => {
      const de = e as DragEvent;
      const t = (de.target as HTMLElement)?.getAttribute?.("title") || (de.target as HTMLElement)?.tagName || "?";
      setState((s) => ({ ...s, lastDragStart: `${t} @${new Date().toLocaleTimeString()}` }));
    };
    const onDrop = (e: Event) => {
      const de = e as DragEvent;
      const p = consumeWorkspaceDragPayload();
      const t = (de.target as HTMLElement)?.getAttribute?.("title") || (de.target as HTMLElement)?.tagName || "?";
      setState((s) => ({
        ...s,
        lastDrop: `${t} payload=${p ? p.path : "(null)"} @${new Date().toLocaleTimeString()}`,
        payloadNow: p ? `${p.path}(${p.is_dir ? "dir" : "file"})` : "(null)",
      }));
    };
    document.addEventListener("dragstart", onDragStart, true);
    document.addEventListener("drop", onDrop, true);
    return () => {
      document.removeEventListener("dragstart", onDragStart, true);
      document.removeEventListener("drop", onDrop, true);
    };
  }, []);

  return (
    <div
      style={{
        position: "fixed", left: 8, bottom: 8, zIndex: 9999, maxWidth: 340,
        background: "rgba(20,20,30,0.92)", color: "#8f8", border: "1px solid #4a4",
        borderRadius: 6, padding: "6px 10px", fontSize: 11, fontFamily: "monospace",
        pointerEvents: "none", whiteSpace: "pre-wrap",
      }}
    >
      <div style={{ color: "#6cf", fontWeight: 600 }}>拖拽诊断（临时）</div>
      <div>dragstart: {state.lastDragStart || "(none)"}</div>
      <div>drop: {state.lastDrop || "(none)"}</div>
      <div>payload: {state.payloadNow || "(none)"}</div>
    </div>
  );
}
