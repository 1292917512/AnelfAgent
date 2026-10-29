import type { ConfigTarget } from "./types";

/** 站内配置地址由 Router 应用 basename，调用方不拼接路径或查询串。 */
export function configHref(target: ConfigTarget): string {
  const params = new URLSearchParams();
  if (target.key) params.set("key", target.key);
  if (target.group) params.set("group", target.group);
  return `/config?${params}`;
}

export function configItemId(key: string): string {
  return `config-item-${key}`;
}
