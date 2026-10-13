import { ToolPresentation } from "@/components/activity/ToolPresentation";
import type { ToolRendererProps } from "@/lib/ui-contributions";

export default function ExecutionView(props: ToolRendererProps) {
  return <ToolPresentation {...props} fields={[{ key: "action" }, { key: "x" }, { key: "y" }, { key: "x2" }, { key: "y2" }, { key: "keys" }, { key: "button" }, { key: "server" }, { key: "tool" }, { key: "op_id" }]} />;
}
