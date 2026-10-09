import { AxiosError, AxiosHeaders } from "axios";
import { describe, expect, it } from "vitest";
import { apiErrorMessage } from "./client";

function responseError(data: unknown) {
  const config = { headers: new AxiosHeaders() };
  return new AxiosError("Request failed", "ERR_BAD_REQUEST", config, undefined, { data, status: 422, statusText: "Unprocessable", headers: {}, config });
}
describe("API errors", () => {
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
