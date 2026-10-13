import { EventStream } from "@/lib/event-stream";
import type { SessionSummary, TraceNode } from "@/lib/types";

export interface ThinkingEvents {
  session_start: { session: SessionSummary; node: TraceNode };
  session_end: { session_id: string; node: TraceNode; summary: SessionSummary };
  node_added: { session_id: string; node: TraceNode };
  node_updated: { session_id: string; node_id: string; updates: Partial<TraceNode> };
  tools_updated: { session_id: string; tools: string[] };
}

type EventHandlers = { [K in keyof ThinkingEvents]: (data: ThinkingEvents[K]) => void };

/** Parses trace events and restores snapshots when frames are lost. */
export class ThinkingStream {
  private readonly stream: EventStream;

  constructor(handlers: EventHandlers, connectionChanged: (connected: boolean) => void, reconnect: () => void) {
    const listen = <K extends keyof ThinkingEvents>(source: EventSource, event: K, isCurrent: () => boolean) => {
      source.addEventListener(event, (message: MessageEvent<string>) => {
        if (!isCurrent()) return;
        try { handlers[event](JSON.parse(message.data)); }
        catch { reconnect(); }
      });
    };
    this.stream = new EventStream("/api/thinking/stream", (source, isCurrent) => {
      listen(source, "session_start", isCurrent);
      listen(source, "session_end", isCurrent);
      listen(source, "node_added", isCurrent);
      listen(source, "node_updated", isCurrent);
      listen(source, "tools_updated", isCurrent);
    }, connectionChanged, (reconnected) => { if (reconnected) reconnect(); }, reconnect);
  }

  start(): void { this.stream.start(); }
  stop(): void { this.stream.stop(); }
}
