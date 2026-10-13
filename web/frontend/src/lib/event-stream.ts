/** Owns one SSE connection and recovers terminal connection failures with bounded backoff. */
export class EventStream {
  private source: EventSource | null = null;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private stopped = true;
  private opened = false;
  private delay = 5000;

  constructor(
    private readonly url: string,
    private readonly attach: (source: EventSource, isCurrent: () => boolean) => void,
    private readonly connectionChanged: (connected: boolean) => void,
    private readonly onOpen: (reconnected: boolean) => void,
    private readonly resync: () => void,
  ) {}

  get active(): boolean { return !this.stopped; }

  start(): void {
    this.stopped = false;
    if (this.source) return;
    clearTimeout(this.timer);
    const source = new EventSource(this.url);
    this.source = source;
    this.attach(source, () => this.source === source);
    source.addEventListener("resync", () => { if (this.source === source) this.resync(); });
    source.onopen = () => {
      if (this.source !== source) return;
      this.delay = 5000;
      this.connectionChanged(true);
      this.onOpen(this.opened);
      this.opened = true;
    };
    source.onerror = () => {
      if (this.source !== source) return;
      this.connectionChanged(false);
      if (source.readyState === EventSource.CLOSED) {
        source.close();
        this.source = null;
        this.timer = setTimeout(() => { if (!this.stopped) this.start(); }, this.delay);
        this.delay = Math.min(this.delay * 2, 30000);
      }
    };
  }

  stop(): void {
    this.stopped = true;
    clearTimeout(this.timer);
    this.source?.close();
    this.source = null;
    this.opened = false;
    this.delay = 5000;
    this.connectionChanged(false);
  }
}
