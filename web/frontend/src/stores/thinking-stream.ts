import type { SessionSummary, TraceNode } from "@/lib/types";

export interface ThinkingEvents {
  session_start: { session: SessionSummary; node: TraceNode };
  session_end: { session_id: string; node: TraceNode; summary: SessionSummary };
  node_added: { session_id: string; node: TraceNode };
  node_updated: { session_id: string; node_id: string; updates: Partial<TraceNode> };
  tools_updated: { session_id: string; tools: string[] };
}

type EventHandlers = { [K in keyof ThinkingEvents]: (data: ThinkingEvents[K]) => void };

/** Maintains one trace stream and resynchronizes after an interrupted connection. */
export class ThinkingStream {
  private source: EventSource | null = null;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private stopped = true;
  private opened = false;

  constructor(
    private readonly handlers: EventHandlers,
    private readonly connectionChanged: (connected: boolean) => void,
    private readonly reconnect: () => void,
  ) {}

  start(): void {
    this.stopped = false;
    if (this.source) return;
    const source = new EventSource("/api/thinking/stream");
    this.source = source;
    source.addEventListener("resync", () => { if (this.source === source) this.reconnect(); });
    source.onopen = () => {
      if (this.source !== source) return;
      this.connectionChanged(true);
      if (this.opened) this.reconnect();
      this.opened = true;
    };
    source.onerror = () => {
      if (this.source !== source) return;
      this.connectionChanged(false);
      if (source.readyState === EventSource.CLOSED) {
        source.close();
        this.source = null;
        this.timer = setTimeout(() => { if (!this.stopped) this.start(); }, 5000);
      }
    };
    this.listen(source, "session_start");
    this.listen(source, "session_end");
    this.listen(source, "node_added");
    this.listen(source, "node_updated");
    this.listen(source, "tools_updated");
  }

  private listen<K extends keyof ThinkingEvents>(source: EventSource, event: K): void {
    source.addEventListener(event, (message: MessageEvent<string>) => {
      if (this.source !== source) return;
      try {
        const data: ThinkingEvents[K] = JSON.parse(message.data);
        this.handlers[event](data);
      } catch {
        this.reconnect();
      }
    });
  }

  stop(): void {
    this.stopped = true;
    clearTimeout(this.timer);
    this.source?.close();
    this.source = null;
    this.opened = false;
    this.connectionChanged(false);
  }
}
