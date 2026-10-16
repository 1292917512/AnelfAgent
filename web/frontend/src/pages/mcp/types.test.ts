import { describe, expect, it } from "vitest";
import { rowsToRecord, serverDraft, serverPatch } from "./types";

const editing = { name: "alpha", config: { transport: "stdio" as const, command: "npx", args: ["-y", "  spaced  "], env: { TOKEN: "********" }, timeout: 12 } };
describe("MCP edits", () => {
  it("sends only changed fields and retains untouched arguments and secrets", () => {
    const draft = serverDraft(editing);
    expect(serverPatch({ ...draft, timeout: "20" }, editing)).toEqual({ timeout: 20 });
    expect(serverPatch({ ...draft, envRows: [], timeout: "" }, editing)).toEqual({ env: {}, timeout: null });
  });
  it("clears the former transport fields on a transport change", () => {
    expect(serverPatch({ ...serverDraft(editing), transport: "streamable_http", url: "https://example.com/mcp" }, editing)).toEqual({
      transport: "streamable_http", url: "https://example.com/mcp", command: null, args: null, env: null,
    });
  });
  it("rejects duplicate keys and nonpositive timeouts, preserving literal property names", () => {
    expect(() => rowsToRecord([{ k: " A ", v: "1" }, { k: "A", v: "2" }])).toThrow("duplicateKey");
    expect(Object.keys(rowsToRecord([{ k: "__proto__", v: "value" }]))).toEqual(["__proto__"]);
    expect(() => serverPatch({ ...serverDraft(editing), callTimeout: "0" }, editing)).toThrow("timeoutPositive");
  });
});
