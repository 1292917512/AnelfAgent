function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function activityRecord(text?: string): Record<string, unknown> {
  try {
    const value: unknown = JSON.parse(text ?? "");
    return isRecord(value) ? value : {};
  } catch { return {}; }
}
