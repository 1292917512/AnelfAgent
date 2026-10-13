import { ToolPresentation } from "@/components/activity/ToolPresentation";
import type { ToolRendererProps } from "@/lib/ui-contributions";

export default function ExecutionView(props: ToolRendererProps) {
  return <ToolPresentation {...props} fields={[{ key: "name" }, { key: "query" }, { key: "marketplace" }, { key: "source" }, { key: "ref" }, { key: "subdir" }, { key: "enabled" }]} />;
}
