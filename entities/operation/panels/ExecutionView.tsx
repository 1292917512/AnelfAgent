import { ToolPresentation } from "@/components/activity/ToolPresentation";
import type { ToolRendererProps } from "@/lib/ui-contributions";
import { activityRecord } from "@/lib/activity-data";
import { DesktopReceiptView } from "./DesktopControlPanel";

export default function ExecutionView(props: ToolRendererProps) {
  const receipt = activityRecord(props.entry.result);
  return <><ToolPresentation {...props} fields={[{ key: "action" }, { key: "x" }, { key: "y" }, { key: "x2" }, { key: "y2" }, { key: "keys" }, { key: "button" }, { key: "server" }, { key: "tool" }, { key: "op_id" }]} />
    {props.view !== "summary" && typeof receipt.process_stopped === "boolean" && <DesktopReceiptView receipt={receipt} />}</>;
}
