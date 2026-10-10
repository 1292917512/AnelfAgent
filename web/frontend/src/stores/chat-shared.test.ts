/** chat-shared 工具函数测试。 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { nextCid } from "./chat-shared";

describe("nextCid", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("安全上下文:用 crypto.randomUUID", () => {
    const id = nextCid();
    expect(id).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("非安全上下文(局域网 HTTP):crypto.randomUUID 缺失时降级且不抛异常", () => {
    vi.stubGlobal("crypto", { randomUUID: undefined });
    const a = nextCid();
    const b = nextCid();
    expect(a).toBeTruthy();
    expect(a).not.toBe(b); // 降级形态仍需唯一
  });

  it("crypto 整体缺失也不抛异常", () => {
    vi.stubGlobal("crypto", undefined);
    expect(() => nextCid()).not.toThrow();
  });
});
