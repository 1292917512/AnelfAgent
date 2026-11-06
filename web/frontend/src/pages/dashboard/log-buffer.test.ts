import { describe, expect, it } from "vitest";
import { MAX_LOG_ENTRIES, mergeLogs, groupAttentionLogs } from "./log-buffer";
import type { LogEntry } from "@/lib/types";

const entry = (seq: number): LogEntry => ({ seq, timestamp: seq, level: "INFO", message: String(seq), tag: "test", time: "12:00:00" });

describe("log buffer", () => {
  it("counts repeated alerts without merging sources or hiding a prior error", () => {
    const error = { ...entry(1), level: "ERROR", message: "offline" };
    const latest = { ...entry(3), level: "WARNING", message: "offline" };
    const groups = groupAttentionLogs([error, latest, latest, { ...error, seq: 2, tag: "other" }]);
    expect(groups).toEqual([{ log: latest, count: 2, danger: true }, { log: { ...error, seq: 2, tag: "other" }, count: 1, danger: true }]);
  });
  it("merges delayed entries in sequence order without duplicate rows", () => {
    expect(mergeLogs([entry(2), entry(4)], [entry(3), entry(2)]).map((row) => row.seq)).toEqual([2, 3, 4]);
  });
  it("bounds retained history under a large burst", () => {
    const rows = mergeLogs([entry(0)], Array.from({ length: 2500 }, (_, i) => entry(i + 1)));
    expect(rows).toHaveLength(MAX_LOG_ENTRIES);
    expect(rows[0]?.seq).toBe(2500 - MAX_LOG_ENTRIES + 1);
    expect(rows[rows.length - 1]?.seq).toBe(2500);
  });
});
