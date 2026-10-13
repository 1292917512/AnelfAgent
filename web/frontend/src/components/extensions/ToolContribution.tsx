import { getUiContributions } from "@/lib/ui-contribution-registry";
import type { ToolRendererProps } from "@/lib/ui-contributions";
import { ContributionContent } from "./ContributionSlot";

/** 仅按后端登记的模块归属选择视图，缺失或失败时保留原始工具展示。 */
export function ToolContribution(props: ToolRendererProps) {
  const renderer = getUiContributions("activity.tool").find((item) => item.owner === props.entry.owner);
  return renderer ? <ContributionContent entry={renderer} componentProps={props} fallback={props.fallback} /> : props.fallback;
}
