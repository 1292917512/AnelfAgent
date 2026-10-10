import { matchPath } from "react-router-dom";
import { Radio, type LucideIcon } from "lucide-react";
import { CORE_ROUTES, type NavigationGroup } from "./core-routes";
import { getUiContributions } from "./ui-contribution-registry";

export interface NavItem {
  path: string;
  label: string;
  description?: string;
  icon: LucideIcon;
  group: NavigationGroup;
  mobile?: boolean;
}
export const NAVIGATION_GROUPS: NavigationGroup[] = ["workspace", "agent", "capabilities", "system"];

export function getNavigation(): NavItem[] {
  const core = CORE_ROUTES.filter((route) => route.navigation !== false);
  const extensions: NavItem[] = getUiContributions("app.routes").map((entry) => ({
    path: entry.path, label: `${entry.title.ns}:${entry.title.key}`,
    description: entry.description ? `${entry.description.ns}:${entry.description.key}` : undefined,
    icon: entry.icon ?? Radio, group: entry.group,
  }));
  return [...core, ...extensions];
}

export function findRoute(pathname: string) {
  return CORE_ROUTES.find((route) => matchPath({ path: route.path, end: true }, pathname))
    ?? getNavigation().find((route) => route.path === pathname);
}
