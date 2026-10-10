import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Wrench } from "lucide-react";
import { Button, Modal } from "@/components/ui";
import { getUiContributions } from "@/lib/ui-contribution-registry";
import { ContributionSlot } from "./ContributionSlot";

export function WorkspaceTools() {
  const { t } = useTranslation("extensions");
  const [open, setOpen] = useState(false);
  if (!getUiContributions("workspace.tools").length) return null;
  return <>
    <Button variant="ghost" size="sm" onClick={() => setOpen(true)} aria-label={t("workspaceTools")} title={t("workspaceTools")}>
      <Wrench size={16} /><span className="hidden sm:inline">{t("tools")}</span>
    </Button>
    <Modal open={open} onClose={() => setOpen(false)} title={t("workspaceTools")}>
      <ContributionSlot slot="workspace.tools" />
    </Modal>
  </>;
}
