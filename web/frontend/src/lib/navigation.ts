import { matchPath } from "react-router-dom";
import { Radio, type LucideIcon } from "lucide-react";
import { CORE_ROUTES, type NavigationGroup } from "./core-routes";
import { listPluginRoutes } from "./channel-plugins";

export interface NavItem {
  path: string;
  label: string;
  icon: LucideIcon;
  group: NavigationGroup;
  mobile?: boolean;
}
export const NAVIGATION_GROUPS: NavigationGroup[] = ["workspace", "agent", "capabilities", "system"];

export function getNavigation(): NavItem[] {
  const core = CORE_ROUTES.filter((route) => route.navigation !== false);
  const paths = new Set(core.map((route) => route.path));
  const plugins: NavItem[] = listPluginRoutes()
    .filter((route) => !paths.has(`/${route.path}`))
    .map((route) => ({ path: `/${route.path}`, label: route.path, icon: Radio, group: "capabilities" }));
  return [...core, ...plugins];
}

export function findRoute(pathname: string) {
  return CORE_ROUTES.find((route) => matchPath({ path: route.path, end: true }, pathname))
    ?? getNavigation().find((route) => route.path === pathname);
}
