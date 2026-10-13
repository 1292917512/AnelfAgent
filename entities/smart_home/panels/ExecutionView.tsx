import { ToolPresentation } from "@/components/activity/ToolPresentation";
import type { ToolRendererProps } from "@/lib/ui-contributions";

export default function ExecutionView(props: ToolRendererProps) {
  return <ToolPresentation {...props} fields={[{ key: "entity_id" }, { key: "action" }, { key: "domain" }, { key: "area" }, { key: "name" }]} />;
}
