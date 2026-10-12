import { expect, it } from "vitest";
import { parseBuiltinTools, parseHeaders, parseJsonObject, validateModelFields } from "./model-editor-data";

it("keeps nested tool parameters and commas inside strings intact", () => {
  expect(parseBuiltinTools('web_search, {"type":"code_interpreter","options":{"text":"a,b","ids":[1,2]}}')).toEqual([
    "web_search", { type: "code_interpreter", options: { text: "a,b", ids: [1, 2] } },
  ]);
});
it("rejects incomplete objects and non-string header values before submitting", () => {
  expect(() => parseBuiltinTools('web_search, {"type":"code')).toThrow();
  expect(() => parseHeaders('{"X-Header":123}')).toThrow("headerValuesRequired");
  expect(() => parseJsonObject("[]")).toThrow("jsonObjectRequired");
  expect(parseHeaders('{"X-Header":"value"}')).toEqual({ "X-Header": "value" });
});

it("rejects built-in tool objects without a usable type", () => {
  for (const text of ['{}', '{"type":42}', '{"type":" "}']) {
    expect(() => parseBuiltinTools(text)).toThrow("builtinToolTypeRequired");
  }
});

it("validates model identity, integer budgets and sampling ranges before saving", () => {
  expect(validateModelFields({ model: "  ", context_window: -1, max_tokens: 1.5, temperature: NaN, top_p: 2 })).toEqual({
    model: "modelIdRequired", context_window: "tokenBudgetInvalid", max_tokens: "tokenBudgetInvalid",
    temperature: "temperatureInvalid", top_p: "topPInvalid",
  });
  expect(validateModelFields({ model: "glm-5.3", context_window: 1000000, max_tokens: null, temperature: null, top_p: 1 })).toEqual({});
});
