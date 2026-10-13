import { ToolPresentation } from "@/components/activity/ToolPresentation";
import type { ToolRendererProps } from "@/lib/ui-contributions";

export default function ExecutionView(props: ToolRendererProps) {
  return <ToolPresentation {...props} fields={[{ key: "entity_name" }, { key: "group" }, { key: "keyword" }, { key: "key" }, { key: "url" }, { key: "mode" }, { key: "enabled" }]} />;
}
