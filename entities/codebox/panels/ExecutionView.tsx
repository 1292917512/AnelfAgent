import { ToolPresentation, CommandExecution, toolText } from "@/components/activity/ToolPresentation";
import type { ToolRendererProps } from "@/lib/ui-contributions";
import { activityRecord } from "@/lib/activity-data";
import { useTranslation } from "react-i18next";

export default function ExecutionView(props: ToolRendererProps) {
  const { t } = useTranslation("workbench");
  const args = activityRecord(props.entry.arguments);
  const command = toolText(args.code);
  if (command) return <CommandExecution {...props} command={command} target={t("toolView.codeExecution")} />;
  return <ToolPresentation {...props} fields={[]} />;
}
