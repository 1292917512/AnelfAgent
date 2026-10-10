import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Wrench } from "lucide-react";
import { Button, Modal } from "@/components/ui";
import { getUiContributions } from "@/lib/ui-contribution-registry";
import { ContributionSlot } from "./ContributionSlot";

export function ContributionTools({ slot }: { slot: "workspace.tools" | "dashboard.actions" }) {
  const { t } = useTranslation("extensions");
  const title = t(slot === "workspace.tools" ? "workspaceTools" : "dashboardTools");
  const [open, setOpen] = useState(false);
  if (!getUiContributions(slot).length) return null;
  return <>
    <Button variant="ghost" size="sm" onClick={() => setOpen(true)} aria-label={title} title={title}>
      <Wrench size={16} /><span className="hidden sm:inline">{slot === "dashboard.actions" ? title : t("tools")}</span>
    </Button>
    <Modal open={open} onClose={() => setOpen(false)} title={title}>
      <ContributionSlot slot={slot} />
    </Modal>
  </>;
}
