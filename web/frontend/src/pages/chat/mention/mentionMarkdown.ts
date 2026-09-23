/** @提及的 markdown 双向序列化：chip ↔ `[name](./path)` 链接。
 *
 * 输入框里 mention 以 markdown 链接形态存在（Codex 式路径信号，不预读内容）；
 * 消息渲染侧经 `splitMentions` 拆分文本，把链接段还原成可点击 chip。
 * 裸路径统一补 `./` 前缀（防渲染器把相对路径当自定义协议），`./` 也被防循环归一剥除。
 */

/** 单个 mention（文本中的一段文件引用） */
export interface FileMention {
  /** 显示名（文件名） */
  name: string;
  /** 工作区相对路径（原始，无 ./ 前缀） */
  path: string;
  /** 该 mention 在文本中的起止（含 markdown 语法字符） */
  start: number;
  end: number;
}

const MENTION_LINK_RE = /\[([^\]]+)\]\(\.\/([^)\s]+)\)/g;

/** 由路径生成 mention 链接文本（`./` 前缀） */
export function mentionMarkdown(name: string, path: string): string {
  return `[${name}](./${path})`;
}

/** 拆分文本为 普通段 / mention 段（按出现顺序） */
export function splitMentions(text: string): Array<string | FileMention> {
  const parts: Array<string | FileMention> = [];
  let last = 0;
  MENTION_LINK_RE.lastIndex = 0;
  for (let m = MENTION_LINK_RE.exec(text); m; m = MENTION_LINK_RE.exec(text)) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    parts.push({ name: m[1] ?? "", path: m[2] ?? "", start: m.index, end: m.index + m[0].length });
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}
