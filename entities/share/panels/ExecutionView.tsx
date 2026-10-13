import { ToolPresentation } from "@/components/activity/ToolPresentation";
import type { ToolRendererProps } from "@/lib/ui-contributions";

export default function ExecutionView(props: ToolRendererProps) {
  return <ToolPresentation {...props} fields={[{ key: "path" }, { key: "share_type" }, { key: "target_url" }, { key: "description" }, { key: "expires_in" }]} />;
}
