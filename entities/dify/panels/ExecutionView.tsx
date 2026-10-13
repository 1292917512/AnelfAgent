import { ToolPresentation } from "@/components/activity/ToolPresentation";
import type { ToolRendererProps } from "@/lib/ui-contributions";

export default function ExecutionView(props: ToolRendererProps) {
  return <ToolPresentation {...props} fields={[{ key: "app" }, { key: "query" }, { key: "name" }, { key: "provider" }, { key: "mode" }, { key: "new_name" }]} />;
}
