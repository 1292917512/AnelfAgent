import { ToolPresentation, CommandExecution, toolText } from "@/components/activity/ToolPresentation";
import type { ToolRendererProps } from "@/lib/ui-contributions";
import { activityRecord } from "@/lib/activity-data";
import { useTranslation } from "react-i18next";

export default function ExecutionView(props: ToolRendererProps) {
  const { t } = useTranslation("workbench");
  const args = activityRecord(props.entry.arguments);
  const command = toolText(args.command);
  const result = activityRecord(props.entry.result);
  if (command) return <CommandExecution {...props} command={command} target={`SSH · ${toolText(result.connection ?? args.name) || t("toolView.defaultConnection")}`} />;
  return <ToolPresentation {...props} fields={[{ key: "name" }, { key: "host" }, { key: "username" }, { key: "local_path" }, { key: "remote_path" }, { key: "proxy_mode" }]} />;
}
