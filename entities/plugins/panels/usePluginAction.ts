import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { apiErrorMessage } from "@/lib/api/client";
import { toast } from "@/stores/toast-store";

/** Keep one lifecycle operation in flight and surface the backend's failure reason. */
export function usePluginAction() {
  const { t } = useTranslation("plugins");
  const pending = useRef(false);
  const [busy, setBusy] = useState("");

  async function run(key: string, action: () => Promise<void>) {
    if (pending.current) return;
    pending.current = true;
    setBusy(key);
    try {
      await action();
    } catch (error) {
      toast.error(apiErrorMessage(error, t("operationFailed")));
    } finally {
      pending.current = false;
      setBusy("");
    }
  }

  return { busy, run };
}
