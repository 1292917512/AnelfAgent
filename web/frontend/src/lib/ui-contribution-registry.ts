import { contributionLoaders } from "@/generated/ui-contributions";
import { loadUiContributions, type RegisteredContribution, type UiSlot } from "./ui-contributions";
import { CORE_ROUTES } from "./core-routes";

let entries: RegisteredContribution[] = [];
let initialization: Promise<void> | undefined;

export function initUiContributions(): Promise<void> {
  return initialization ??= loadUiContributions(contributionLoaders, [
    ...CORE_ROUTES.map((route) => `app.routes:${route.path}`),
    ...["database", "volumes", "storage"].map((tab) => `data.tabs:${tab}`),
  ]).then((loaded) => { entries = loaded; });
}

export function getUiContributions<S extends UiSlot>(slot: S): readonly RegisteredContribution<S>[] {
  return entries.filter((entry): entry is RegisteredContribution<S> => entry.slot === slot);
}
