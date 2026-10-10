import { defineUiContributions } from "@/lib/ui-contributions";

export default defineUiContributions([
  {
    id: "service-tools", slot: "workspace.tools", order: 50,
    title: { ns: "devops", key: "serviceControl" },
    description: { ns: "devops", key: "quickControlsDesc" },
    load: () => import("./ServiceControls"),
  },
  {
    id: "service-card", slot: "dashboard.cards", order: 50,
    title: { ns: "devops", key: "serviceControl" },
    description: { ns: "devops", key: "quickControlsDesc" },
    load: () => import("./ServiceControls"),
  },
]);
