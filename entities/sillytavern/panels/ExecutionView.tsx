import { ToolPresentation } from "@/components/activity/ToolPresentation";
import type { ToolRendererProps } from "@/lib/ui-contributions";

export default function ExecutionView(props: ToolRendererProps) {
  return <ToolPresentation {...props} fields={[{ key: "avatar" }, { key: "name" }, { key: "query" }, { key: "field" }, { key: "collection_id" }, { key: "remote" }, { key: "branch" }]} />;
}
