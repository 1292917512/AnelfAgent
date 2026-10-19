import type { ActivityDelta, ActivityRun, ActivitySnapshot } from "./types/activity";

/** 连接内重建过程增量；独立于 HTTP 快照，避免两条传输的修订交错破坏正文基线。 */
export class ActivityStreamDecoder {
  private epoch = "";
  private runs = new Map<string, ActivityRun>();

  receive(epoch: string, run: ActivityRun): ActivityRun {
    if (epoch !== this.epoch) { this.epoch = epoch; this.runs.clear(); }
    this.runs.delete(run.id);
    this.runs.set(run.id, run);
    while (this.runs.size > 20) this.runs.delete(this.runs.keys().next().value!);
    return run;
  }

  restore(snapshot: ActivitySnapshot): void {
    this.runs.clear();
    this.epoch = snapshot.epoch;
    for (const run of snapshot.runs) this.receive(snapshot.epoch, run);
  }

  apply(delta: ActivityDelta): ActivityRun | null {
    const previous = this.epoch === delta.epoch ? this.runs.get(delta.run.id) : undefined;
    if (!previous) return null;
    if (previous.revision >= delta.run.revision) return previous;
    if (previous.revision !== delta.base_revision) return null;
    const entries = new Map(previous.entries.map((entry) => [entry.id, entry]));
    for (const entry of delta.entries) entries.set(entry.id, entry);
    for (const append of delta.appends) {
      const entry = entries.get(append.id);
      if (!entry || (entry.kind !== "text" && entry.kind !== "thinking")) return null;
      entries.set(entry.id, { ...entry, content: entry.content + append.text });
    }
    const order = delta.entry_order ?? previous.entries.map((entry) => entry.id);
    if (order.some((id) => !entries.has(id))) return null;
    return this.receive(delta.epoch, { ...previous, ...delta.run, entries: order.map((id) => entries.get(id)!) });
  }
}
