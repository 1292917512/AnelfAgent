import { describe, expect, it } from "vitest";
import { MAX_LOG_ENTRIES, mergeLogs } from "./log-buffer";
import type { LogEntry } from "@/lib/types";

const entry = (seq: number): LogEntry => ({ seq, timestamp: seq, level: "INFO", message: String(seq), tag: "test", time: "12:00:00" });

describe("log buffer", () => {
  it("merges delayed entries in sequence order without duplicate rows", () => {
    expect(mergeLogs([entry(2), entry(4)], [entry(3), entry(2)]).map((row) => row.seq)).toEqual([2, 3, 4]);
  });
  it("bounds retained history under a large burst", () => {
    const rows = mergeLogs([entry(0)], Array.from({ length: 2500 }, (_, i) => entry(i + 1)));
    expect(rows).toHaveLength(MAX_LOG_ENTRIES);
    expect(rows[0]?.seq).toBe(501);
    expect(rows[rows.length - 1]?.seq).toBe(2500);
  });
});
