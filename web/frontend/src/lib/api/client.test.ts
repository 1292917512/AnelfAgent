import { AxiosError, AxiosHeaders } from "axios";
import { describe, expect, it, vi } from "vitest";
import { api, apiErrorMessage, setApiErrorHandler, setUnauthorizedHandler } from "./client";

function responseError(data: unknown) {
  const config = { headers: new AxiosHeaders() };
  return new AxiosError("Request failed", "ERR_BAD_REQUEST", config, undefined, { data, status: 422, statusText: "Unprocessable", headers: {}, config });
}
describe("API errors", () => {
  it("assigns error feedback to one owner while keeping authentication recovery independent", async () => {
    const globalError = vi.fn();
    const unauthorized = vi.fn();
    setApiErrorHandler(globalError); setUnauthorizedHandler(unauthorized);
    const adapter: NonNullable<Parameters<typeof api.post>[2]>["adapter"] = async (config) => {
      throw new AxiosError("Request failed", "ERR_BAD_REQUEST", config, undefined,
        { data: { detail: "Expired" }, status: 401, statusText: "Unauthorized", headers: {}, config });
    };
    try {
      await expect(api.post("/owned", { name: "Draft" }, { adapter, errorFeedback: "local" })).rejects.toThrow();
      expect(globalError).not.toHaveBeenCalled();
      expect(unauthorized).toHaveBeenCalledOnce();
      await expect(api.post("/fallback", {}, { adapter })).rejects.toThrow();
      expect(globalError).toHaveBeenCalledOnce();
    } finally { setApiErrorHandler(null); setUnauthorizedHandler(null); }
  });
  it("renders FastAPI validation errors as text", () => {
    expect(apiErrorMessage(responseError({ detail: [{ loc: ["body", "name"], msg: "Name is required" }, { msg: "Port must be positive" }] }), "fallback")).toBe("Name is required; Port must be positive");
  });
  it("reads service error responses", () => {
    expect(apiErrorMessage(responseError({ error: "Invalid password" }), "fallback")).toBe("Invalid password");
  });
  it("never returns server objects as React content", () => {
    expect(typeof apiErrorMessage(responseError({ detail: { unexpected: 123 } }), "fallback")).toBe("string");
    expect(apiErrorMessage(null, "fallback")).toBe("fallback");
  });
});
