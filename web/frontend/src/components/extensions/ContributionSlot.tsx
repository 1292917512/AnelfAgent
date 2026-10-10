import { Component, Suspense, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { getUiContributions } from "@/lib/ui-contribution-registry";
import type { RegisteredContribution, UiSlot, UiSlotProps } from "@/lib/ui-contributions";
import { Card } from "@/components/common/Card";

class ContributionBoundary extends Component<{ children: ReactNode; fallback: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: Error) { console.error("[ui-contributions] Render failed", error); }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

type ContextSlot = "audio.recording.actions" | "chat.message";
type ContentProps = { [S in ContextSlot]: { entry: RegisteredContribution<S>; componentProps: UiSlotProps[S] } }[ContextSlot]
  | { entry: RegisteredContribution<Exclude<UiSlot, ContextSlot>>; componentProps: Record<string, never> };

export function ContributionContent({ entry, componentProps }: ContentProps) {
  const { t } = useTranslation("extensions");
  let content: ReactNode;
  if (entry.slot === "chat.message" && "payload" in componentProps) {
    const Content = entry.Component;
    content = <Content payload={componentProps.payload} />;
  } else if (entry.slot === "audio.recording.actions" && "path" in componentProps) {
    const Content = entry.Component;
    content = <Content path={componentProps.path} />;
  } else if (entry.slot !== "chat.message" && entry.slot !== "audio.recording.actions") {
    const Content = entry.Component;
    content = <Content />;
  }
  return <ContributionBoundary fallback={<p role="alert" className="text-sm text-muted">{t("unavailable")}</p>}>
    <Suspense fallback={<span role="status" className="text-xs text-muted">{t("common:loading")}</span>}>{content}</Suspense>
  </ContributionBoundary>;
}

function Contribution({ entry }: { entry: RegisteredContribution<"workspace.tools" | "dashboard.cards" | "dashboard.actions" | "audio.identify.after"> }) {
  const { t } = useTranslation("extensions");
  return <Card title={t(entry.title.key, { ns: entry.title.ns })}
    subtitle={entry.description ? t(entry.description.key, { ns: entry.description.ns }) : undefined}>
    <ContributionContent entry={entry} componentProps={{}} />
  </Card>;
}

export function ContributionSlot({ slot }: { slot: "workspace.tools" | "dashboard.cards" | "dashboard.actions" | "audio.identify.after" }) {
  const entries = getUiContributions(slot);
  if (!entries.length) return null;
  return <div className={slot === "dashboard.cards" ? "grid grid-cols-1 gap-4 lg:grid-cols-2" : "space-y-3"}>
    {entries.map((entry) => <Contribution key={entry.key} entry={entry} />)}
  </div>;
}

type InlineProps = { slot: "audio.recording.actions"; componentProps: UiSlotProps["audio.recording.actions"] }
  | { slot: "system.restart" | "audio.overview.cards"; componentProps: Record<string, never> };
export function InlineContributions(props: InlineProps) {
  if (props.slot === "audio.recording.actions") return getUiContributions(props.slot).map((entry) => <ContributionContent key={entry.key} entry={entry} componentProps={props.componentProps} />);
  return getUiContributions(props.slot).map((entry) => <ContributionContent key={entry.key} entry={entry} componentProps={{}} />);
}
