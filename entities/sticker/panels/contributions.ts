import { Smile } from "lucide-react";
import { defineUiContributions } from "@/lib/ui-contributions";

export default defineUiContributions([
  { id: "library-page", slot: "app.routes", path: "/stickers", group: "capabilities", icon: Smile,
    title: { ns: "sticker", key: "title" }, description: { ns: "sticker", key: "subtitle" }, load: () => import("./LibraryPage") },
  { id: "library-tab", slot: "data.tabs", tab: "stickers", icon: Smile,
    title: { ns: "sticker", key: "title" }, load: () => import("./library/StickersPanel").then((module) => ({ default: module.StickersPanel })) },
]);
