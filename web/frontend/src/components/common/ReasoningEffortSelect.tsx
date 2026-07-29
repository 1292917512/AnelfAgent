import type { TFunction } from "i18next";
import type { ReasoningEffort } from "@/lib/types";

/** reasoning effort 的 7 个等级（与后端 ReasoningEffort 一一对应） */
export const REASONING_EFFORT_VALUES = [
  "off",
  "minimal",
  "low",
  "medium",
  "high",
  "xhigh",
  "max",
] as const satisfies readonly ReasoningEffort[];

function cap(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/**
 * 渲染 7 个 reasoning effort <option>（label 取 t(`${keyPrefix}effort{Off|Minimal|...}`)）。
 * 各页面命名空间前缀不同：config 页 "tasks."、heartbeat 页 "schedule."、models 页无前缀。
 * 空值/"跟随全局"等首选项由调用方按语义自行添加。
 */
export function ReasoningEffortOptions({ t, keyPrefix = "" }: { t: TFunction; keyPrefix?: string }) {
  return (
    <>
      {REASONING_EFFORT_VALUES.map((v) => (
        <option key={v} value={v}>{t(`${keyPrefix}effort${cap(v)}`)}</option>
      ))}
    </>
  );
}
