import type { TFunction } from "i18next";
import i18n from "@/i18n";
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
 * 渲染 7 个 reasoning effort <option>（label 取 t(`${keyPrefix}effort{Off|Minimal|...}`)，
 * 档位名后括号标注下发给端点的具体等级标签，如「深度（high）」）。
 * 各页面命名空间前缀不同：config 页 "tasks."、heartbeat 页 "schedule."、models 页无前缀。
 * 空值/「跟随模型」等首选项由调用方按语义自行添加。
 */
export function ReasoningEffortOptions({ t, keyPrefix = "" }: { t: TFunction; keyPrefix?: string }) {
  const [lp, rp] = i18n.language?.startsWith("zh") ? ["（", "）"] : [" (", ")"];
  return (
    <>
      {REASONING_EFFORT_VALUES.map((v) => (
        <option key={v} value={v}>{`${t(`${keyPrefix}effort${cap(v)}`)}${lp}${v}${rp}`}</option>
      ))}
    </>
  );
}

/** 取思考契约 map 里该模型支持的最高规范档（不含 off）；无 map（开关型/无契约）取词汇表最高档 max */
export function highestContractEffort(thinking: unknown): ReasoningEffort {
  if (thinking && typeof thinking === "object" && !Array.isArray(thinking) && "map" in thinking) {
    const { map } = thinking;
    if (map && typeof map === "object" && !Array.isArray(map)) {
      const ranked = REASONING_EFFORT_VALUES.filter((v) => v !== "off" && v in map);
      return ranked[ranked.length - 1] ?? "max";
    }
  }
  return "max";
}
