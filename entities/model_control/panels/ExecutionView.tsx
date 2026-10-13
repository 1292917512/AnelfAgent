import { ToolPresentation } from "@/components/activity/ToolPresentation";
import type { ToolRendererProps } from "@/lib/ui-contributions";

export default function ExecutionView(props: ToolRendererProps) {
  return <ToolPresentation {...props} fields={[{ key: "model_name" }, { key: "model_id" }, { key: "model_ids" }, { key: "model_type" }, { key: "provider_id" }, { key: "name" }, { key: "field" }, { key: "models" }]} />;
}
