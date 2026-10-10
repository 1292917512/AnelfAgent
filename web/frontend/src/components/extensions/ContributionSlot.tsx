import { Component, Suspense, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { getUiContributions } from "@/lib/ui-contribution-registry";
import type { RegisteredContribution, UiSlot } from "@/lib/ui-contributions";
import { Card } from "@/components/common/Card";

class ContributionBoundary extends Component<{ children: ReactNode; fallback: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: Error) { console.error("[ui-contributions] Render failed", error); }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

function Contribution({ entry }: { entry: RegisteredContribution }) {
  const { t } = useTranslation("extensions");
  const Content = entry.Component;
  return <Card title={t(entry.title.key, { ns: entry.title.ns })}
    subtitle={entry.description ? t(entry.description.key, { ns: entry.description.ns }) : undefined}>
    <ContributionBoundary fallback={<p role="alert" className="text-sm text-muted">{t("unavailable")}</p>}>
      <Suspense fallback={<div role="status" className="h-14 animate-pulse rounded-xl bg-elevated"><span className="sr-only">{t("common:loading")}</span></div>}><Content /></Suspense>
    </ContributionBoundary>
  </Card>;
}

export function ContributionSlot({ slot }: { slot: UiSlot }) {
  const entries = getUiContributions(slot);
  if (!entries.length) return null;
  return <div className={slot === "dashboard.cards" ? "grid grid-cols-1 gap-4 lg:grid-cols-2" : "space-y-3"}>
    {entries.map((entry) => <Contribution key={entry.key} entry={entry} />)}
  </div>;
}
