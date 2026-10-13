import { ToolPresentation } from "@/components/activity/ToolPresentation";
import type { ToolRendererProps } from "@/lib/ui-contributions";

export default function ExecutionView(props: ToolRendererProps) {
  return <ToolPresentation {...props} fields={[{ key: "query" }, { key: "source_path" }, { key: "image_path" }, { key: "sticker_id" }, { key: "channel_id" }, { key: "target_id" }, { key: "emotion" }]} />;
}
