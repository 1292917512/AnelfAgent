import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useUnsavedChanges } from "@/components/common/UnsavedChanges";

export function useDiscardChanges(dirty: boolean, onClose: () => void, busy = false) {
  const { t } = useTranslation("common");
  const [confirming, setConfirming] = useState(false);
  useUnsavedChanges(dirty);
  return {
    requestClose: () => { if (!busy) { if (dirty) setConfirming(true); else onClose(); } },
    confirmProps: {
      open: confirming, title: t("unsavedChanges"), message: t("unsavedDescription"),
      confirmText: t("discardChanges"), cancelText: t("keepEditing"), danger: true,
      onClose: () => setConfirming(false),
      onConfirm: () => { setConfirming(false); onClose(); },
    },
  };
}
