import { expect, it } from "vitest";
import { parseBuiltinTools, parseHeaders, parseJsonObject } from "./model-editor-data";

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
