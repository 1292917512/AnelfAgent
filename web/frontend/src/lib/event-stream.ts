import type { StreamStatus } from "./types/chat";

const STALE_AFTER_MS = 90_000;
const HEALTH_CHECK_MS = 10_000;

/** 单个 SSE 连接的生命周期与收帧检测；恢复不改变领域数据和修订基线。 */
export class EventStream {
  private source: EventSource | null = null;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private healthTimer: ReturnType<typeof setInterval> | undefined;
  private stopped = true;
  private opened = false;
  private delay = 5000;
  private lastReceived = 0;
  private status: StreamStatus = "stopped";
  private detach: (() => void) | undefined;
  private readonly onVisibilityChange = () => {
    if (!this.stopped && document.visibilityState === "visible") {
      this.lastReceived = performance.now();
      this.resync();
    }
  };

  constructor(
    private readonly url: string,
    private readonly attach: (source: EventSource, isCurrent: () => boolean, recover: () => void, received: () => void) => void | (() => void),
    private readonly connectionChanged: (connected: boolean, status: StreamStatus) => void,
    private readonly onOpen: (reconnected: boolean) => void,
    private readonly resync: () => void,
  ) {}

  get active(): boolean { return !this.stopped; }

  private changeStatus(status: StreamStatus): void {
    if (this.status === status) return;
    this.status = status;
    this.connectionChanged(status === "connected", status);
  }

  start(): void {
    if (this.stopped) document.addEventListener("visibilitychange", this.onVisibilityChange);
    this.stopped = false;
    if (this.source) return;
    clearTimeout(this.timer);
    const source = new EventSource(this.url);
    this.source = source;
    this.lastReceived = performance.now();
    this.changeStatus(this.opened ? "reconnecting" : "connecting");
    const recover = (status: StreamStatus = "reconnecting") => {
      if (this.source !== source) return;
      this.changeStatus(status);
      clearInterval(this.healthTimer);
      this.detach?.(); this.detach = undefined;
      source.close();
      this.source = null;
      this.timer = setTimeout(() => { if (!this.stopped) this.start(); }, this.delay);
      this.delay = Math.min(this.delay * 2, 30000);
    };
    const received = () => {
      if (this.source !== source) return;
      this.lastReceived = performance.now();
      this.changeStatus("connected");
    };
    const detach = this.attach(source, () => this.source === source, () => recover(), received);
    this.detach = typeof detach === "function" ? detach : undefined;
    source.addEventListener("ping", received);
    source.addEventListener("resync", () => { if (this.source === source) { received(); this.resync(); } });
    this.healthTimer = setInterval(() => {
      if (this.source === source && !document.hidden && performance.now() - this.lastReceived >= STALE_AFTER_MS) recover("stalled");
    }, HEALTH_CHECK_MS);
    source.onopen = () => {
      if (this.source !== source) return;
      this.delay = 5000;
      received();
      this.onOpen(this.opened);
      this.opened = true;
    };
    source.onerror = () => {
      if (this.source !== source) return;
      if (source.readyState === EventSource.CLOSED) recover();
      else this.changeStatus("reconnecting");
    };
  }

  stop(): void {
    this.stopped = true;
    document.removeEventListener("visibilitychange", this.onVisibilityChange);
    clearTimeout(this.timer);
    clearInterval(this.healthTimer);
    this.detach?.(); this.detach = undefined;
    this.source?.close();
    this.source = null;
    this.opened = false;
    this.delay = 5000;
    this.changeStatus("stopped");
  }
}
