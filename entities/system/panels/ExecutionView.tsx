import { ToolPresentation } from "@/components/activity/ToolPresentation";
import type { ToolRendererProps } from "@/lib/ui-contributions";

export default function ExecutionView(props: ToolRendererProps) {
  return <ToolPresentation {...props} fields={[{ key: "packages" }, { key: "keyword" }, { key: "tag" }, { key: "level" }, { key: "domain" }, { key: "provider" }, { key: "key" }]} />;
}
