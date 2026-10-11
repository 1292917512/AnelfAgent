/** 设备状态值展示格式化 — 把平台原始值转为人可读文本。 */

const ISO_RE = /^\d{4}-\d{2}-\d{2}T[\d:.]+(?:Z|[+-]\d{2}:?\d{2})$/;

/** ISO8601 时间戳转本地短格式（如 10-11 21:29），非时间戳返回 null。 */
export function formatIsoTimestamp(value: string, locale: string): string | null {
  if (!ISO_RE.test(value)) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat(locale, {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}
