import { ToolPresentation } from "@/components/activity/ToolPresentation";
import type { ToolRendererProps } from "@/lib/ui-contributions";

export default function ExecutionView(props: ToolRendererProps) {
  return <ToolPresentation {...props} fields={[{ key: "action" }, { key: "module" }, { key: "title" }, { key: "date" }, { key: "time" }, { key: "event_id" }]} />;
}
