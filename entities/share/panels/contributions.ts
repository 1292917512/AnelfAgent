import { defineUiContributions } from "@/lib/ui-contributions";

export default defineUiContributions([
  { id: "chat-card", slot: "chat.message", messageType: "share.link", title: { ns: "share", key: "types.link.name" }, load: () => import("./ChatCard") },
]);
