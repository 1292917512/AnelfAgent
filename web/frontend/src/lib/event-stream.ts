/** Owns one SSE connection and recovers terminal connection failures with bounded backoff. */
export class EventStream {
  private source: EventSource | null = null;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private stopped = true;
  private opened = false;
  private delay = 5000;
  private readonly onVisibilityChange = () => {
    if (!this.stopped && document.visibilityState === "visible") this.resync();
  };

  constructor(
    private readonly url: string,
    private readonly attach: (source: EventSource, isCurrent: () => boolean, recover: () => void) => void,
    private readonly connectionChanged: (connected: boolean) => void,
    private readonly onOpen: (reconnected: boolean) => void,
    private readonly resync: () => void,
  ) {}

  get active(): boolean { return !this.stopped; }

  start(): void {
    if (this.stopped) document.addEventListener("visibilitychange", this.onVisibilityChange);
    this.stopped = false;
    if (this.source) return;
    clearTimeout(this.timer);
    const source = new EventSource(this.url);
    this.source = source;
    const recover = () => {
      if (this.source !== source) return;
      this.connectionChanged(false);
      source.close();
      this.source = null;
      this.timer = setTimeout(() => { if (!this.stopped) this.start(); }, this.delay);
      this.delay = Math.min(this.delay * 2, 30000);
    };
    this.attach(source, () => this.source === source, recover);
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
      if (source.readyState === EventSource.CLOSED) recover();
      else this.connectionChanged(false);
    };
  }

  stop(): void {
    this.stopped = true;
    document.removeEventListener("visibilitychange", this.onVisibilityChange);
    clearTimeout(this.timer);
    this.source?.close();
    this.source = null;
    this.opened = false;
    this.delay = 5000;
    this.connectionChanged(false);
  }
}
