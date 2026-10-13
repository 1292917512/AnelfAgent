import { defineUiContributions } from "@/lib/ui-contributions";

export default defineUiContributions([
  { id: "execution", slot: "activity.tool", title: { ns: "workbench", key: "activity.result" }, load: () => import("./ExecutionView") },
  { id: "restart-control", slot: "system.restart", title: { ns: "devops", key: "restartService" }, load: () => import("./RestartControl") },
  {
    id: "service-tools", slot: "workspace.tools", order: 50,
    title: { ns: "devops", key: "serviceControl" },
    description: { ns: "devops", key: "quickControlsDesc" },
    load: () => import("./ServiceControls"),
  },
  {
    id: "service-actions", slot: "dashboard.actions", order: 50,
    title: { ns: "devops", key: "serviceControl" },
    description: { ns: "devops", key: "quickControlsDesc" },
    load: () => import("./ServiceControls"),
  },
]);
