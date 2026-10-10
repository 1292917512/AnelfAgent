import type { ChatExtension } from "@/lib/types/chat";
import { getUiContributions } from "@/lib/ui-contribution-registry";
import { ContributionContent } from "./ContributionSlot";

export function MessageExtension({ extension }: { extension: ChatExtension }) {
  const entry = getUiContributions("chat.message").find((item) => item.messageType === extension.type);
  return entry ? <ContributionContent entry={entry} componentProps={{ payload: extension.payload }} />
    : <p className="whitespace-pre-wrap break-words text-sm text-muted">{extension.fallback}</p>;
}
