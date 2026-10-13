import { ToolPresentation, CommandExecution, toolText } from "@/components/activity/ToolPresentation";
import type { ToolRendererProps } from "@/lib/ui-contributions";
import { activityRecord } from "@/lib/activity-data";
import { useTranslation } from "react-i18next";

export default function ExecutionView(props: ToolRendererProps) {
  const { t } = useTranslation("workbench");
  const args = activityRecord(props.entry.arguments);
  const command = toolText(args.command ?? args.code);
  if (command) return <CommandExecution {...props} command={command} target={typeof args.code === "string" ? "Python" : t("toolView.localShell")} />;
  return <ToolPresentation {...props} fields={[{ key: "file_path" }, { key: "path" }, { key: "src" }, { key: "dst" }, { key: "pattern" }, { key: "content_pattern" }, { key: "offset" }, { key: "limit" }]} />;
}
