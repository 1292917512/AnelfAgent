import type { SkillMCPStatus } from "./types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function activityRecord(text?: string): Record<string, unknown> {
  try {
    const value: unknown = JSON.parse(text ?? "");
    return isRecord(value) ? value : {};
  } catch { return {}; }
}

export function skillMcpStates(value: unknown): SkillMCPStatus[] {
  if (!Array.isArray(value)) return [];
  return value.slice(0, 16).flatMap((item: unknown) => {
    if (!isRecord(item) || typeof item.name !== "string" || !(typeof item.configured === "boolean" || item.configured === null)) return [];
    const state: SkillMCPStatus = { name: item.name, configured: item.configured };
    if (typeof item.enabled === "boolean") state.enabled = item.enabled;
    if (typeof item.connected === "boolean") state.connected = item.connected;
    if (typeof item.tool_count === "number") state.tool_count = item.tool_count;
    if (item.activation === "active" || item.activation === "sleeping" || item.activation === "deactivated" || item.activation === "unavailable" || item.activation === "on_demand") state.activation = item.activation;
    return [state];
  });
}
