import { defineUiContributions } from "@/lib/ui-contributions";

export default defineUiContributions([
  { id: "execution", slot: "activity.tool", title: { ns: "workbench", key: "activity.result" }, load: () => import("./ExecutionView") },
]);
