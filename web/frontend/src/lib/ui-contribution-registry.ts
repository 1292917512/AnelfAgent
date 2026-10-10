import { contributionLoaders } from "@/generated/ui-contributions";
import { loadUiContributions, type RegisteredContribution, type UiSlot } from "./ui-contributions";

let entries: RegisteredContribution[] = [];
let initialization: Promise<void> | undefined;

export function initUiContributions(): Promise<void> {
  return initialization ??= loadUiContributions(contributionLoaders).then((loaded) => { entries = loaded; });
}

export function getUiContributions(slot: UiSlot): readonly RegisteredContribution[] {
  return entries.filter((entry) => entry.slot === slot);
}
