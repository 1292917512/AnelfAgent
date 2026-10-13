import { defineUiContributions } from "@/lib/ui-contributions";

export default defineUiContributions([
  { id: "execution", slot: "activity.tool", title: { ns: "workbench", key: "activity.result" }, load: () => import("./ExecutionView") },
  { id: "chat-card", slot: "chat.message", messageType: "share.link", title: { ns: "share", key: "types.link.name" }, load: () => import("./ChatCard") },
]);
