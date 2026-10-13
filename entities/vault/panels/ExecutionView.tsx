import { ToolPresentation } from "@/components/activity/ToolPresentation";
import type { ToolRendererProps } from "@/lib/ui-contributions";

export default function ExecutionView(props: ToolRendererProps) {
  return <ToolPresentation {...props} fields={[{ key: "query" }, { key: "tag" }, { key: "entry_id" }, { key: "field" }, { key: "title" }, { key: "favorite" }]} />;
}
