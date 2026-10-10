export interface MessageTag { key: string; value: string; start: number; end: number }

/** 与 core/tags.py 同构解码；标签只影响展示，不执行其中的内容。 */
export function parseMessageTags(content: string): MessageTag[] {
  const pattern = /\[([\p{L}\p{N}_]+):((?:\\.|[^\][\\\r\n])*)\]/gu;
  const escapes: Record<string, string> = { "\\": "\\", "[": "[", "]": "]", u000a: "\n", u000d: "\r", u0009: "\t" };
  return Array.from(content.matchAll(pattern), (match) => ({
    key: match[1]!, value: match[2]!.replace(/\\([\\[\]]|u000[ad9])/g, (_, key: string) => escapes[key]!),
    start: match.index, end: match.index + match[0].length,
  }));
}
