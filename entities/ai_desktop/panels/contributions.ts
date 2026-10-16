import { defineUiContributions } from "@/lib/ui-contributions";

export default defineUiContributions([
  { id: "bailian-console", slot: "credentials.editor", title: { ns: "ai_desktop", key: "subscription.credentialsTitle" }, load: () => import("./BailianCredential") },
  { id: "execution", slot: "activity.tool", title: { ns: "workbench", key: "activity.result" }, load: () => import("./ExecutionView") },
]);
