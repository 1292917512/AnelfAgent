import type { JsonObject, JsonValue } from "@/lib/types";

function isJsonValue(value: unknown): value is JsonValue {
  return value === null || typeof value === "string" || typeof value === "boolean" ||
    typeof value === "number" || (Array.isArray(value) ? value.every(isJsonValue) : isJsonObject(value));
}

export function isJsonObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value) && Object.values(value).every(isJsonValue);
}

export function parseJsonObject(text: string): JsonObject {
  const value: unknown = JSON.parse(text || "{}");
  if (!isJsonObject(value)) throw new Error("jsonObjectRequired");
  return value;
}

export function parseHeaders(text: string): Record<string, string> {
  const object = parseJsonObject(text);
  const headers: Record<string, string> = {};
  for (const [key, value] of Object.entries(object)) {
    if (typeof value !== "string") throw new Error("headerValuesRequired");
    headers[key] = value;
  }
  return headers;
}

/** Splits tool names and JSON objects at commas outside nested values and strings. */
export function parseBuiltinTools(text: string): Array<string | JsonObject> {
  const tokens: string[] = [];
  let depth = 0;
  let inString = false;
  let escaped = false;
  let token = "";
  for (const char of text) {
    if (escaped) { token += char; escaped = false; continue; }
    if (char === "\\" && inString) { token += char; escaped = true; continue; }
    if (char === '"') inString = !inString;
    if (!inString) {
      if (char === "{" || char === "[") depth++;
      if (char === "}" || char === "]") depth--;
      if (depth < 0) throw new Error("invalidJson");
      if (char === "," && depth === 0) { tokens.push(token.trim()); token = ""; continue; }
    }
    token += char;
  }
  if (depth !== 0 || inString) throw new Error("invalidJson");
  tokens.push(token.trim());
  return tokens.filter(Boolean).map((value) => value.startsWith("{") ? parseJsonObject(value) : value);
}
