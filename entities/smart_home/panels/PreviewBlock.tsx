/** 与后端房间快照契约对齐；无法识别的诊断消息按原文完整展示。 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Braces, Eye, List, RefreshCw } from "lucide-react";
import { Card } from "@/components/common/Card";
import { Button, EmptyState } from "@/components/ui";
import type { PreviewResponse } from "./types";

function parseSnapshot(content: string) {
  const rooms: Array<{ name: string; devices: string[] }> = [];
  const notes: string[] = [];
  for (const line of content.split("\n").map((value) => value.trim()).filter(Boolean)) {
    if (line === "[智能家居]") continue;
    const currentRoom = rooms[rooms.length - 1];
    if (line.startsWith("- ") && currentRoom) currentRoom.devices.push(line.slice(2));
    else if (line.endsWith(":")) rooms.push({ name: line.slice(0, -1), devices: [] });
    else notes.push(line);
  }
  return rooms.length && rooms.every((room) => room.devices.length) ? { rooms, notes } : null;
}

export function PreviewBlock({ preview, onRefresh }: { preview?: PreviewResponse; onRefresh: () => void }) {
  const { t } = useTranslation("smart_home");
  const [raw, setRaw] = useState(false);
  const content = preview?.content ?? "";
  const snapshot = parseSnapshot(content);
  return <Card title={t("preview.title")} subtitle={preview?.activation_required ? t("preview.onDemand") : t("preview.subtitle")}
    actions={<>
      <Button variant="ghost" size="sm" onClick={() => setRaw((value) => !value)} title={raw ? t("preview.structured") : t("preview.raw")}>
        {raw ? <List size={14} /> : <Braces size={14} />}{raw ? t("preview.structured") : t("preview.raw")}
      </Button>
      <Button variant="ghost" size="icon" onClick={onRefresh} title={t("preview.refresh")}><RefreshCw size={14} /></Button>
    </>}>
    {!preview?.injecting || !content ? <EmptyState icon={Eye} title={t("preview.empty")} className="py-8" />
      : raw || snapshot === null ? <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-xl border border-border bg-elevated p-4 font-mono text-xs leading-relaxed text-foreground">{content}</pre>
      : <div className="max-h-[32rem] space-y-4 overflow-auto">
        {snapshot.rooms.map((room, index) => <section key={index} className="rounded-xl border border-border bg-elevated/40 p-4">
          <h4 className="mb-2 text-xs font-semibold text-heading">{room.name}</h4>
          <ul className="space-y-2">{room.devices.map((device, deviceIndex) => <li key={deviceIndex} className="break-words text-xs leading-relaxed text-foreground">{device}</li>)}</ul>
        </section>)}
        {snapshot.notes.map((note, index) => <p key={index} className="text-xs leading-relaxed text-muted">{note}</p>)}
      </div>}
  </Card>;
}
