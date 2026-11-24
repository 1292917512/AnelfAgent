import { Puzzle } from "lucide-react";
import { defineUiContributions } from "@/lib/ui-contributions";

export default defineUiContributions([
  { id: "plugins-page", slot: "app.routes", path: "/plugins", group: "capabilities", icon: Puzzle,
    title: { ns: "plugins", key: "title" }, load: () => import("./PluginsPage") },
  { id: "execution", slot: "activity.tool", title: { ns: "workbench", key: "activity.result" }, load: () => import("./ExecutionView") },
]);
