import { lazy, Suspense, useCallback, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ActivityInspection } from "@/components/common/ActivityInspection";
import { LoadingBlock, Modal } from "@/components/ui";

const ActivityDetailsDialog = lazy(() => import("./ActivityDetailsDialog"));

/** 按需加载过程查看器，关闭后释放详情组件。 */
export function ActivityDetailsProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation("settings");
  const [ids, setIds] = useState<string[]>([]);
  const [open, setOpen] = useState(false);
  const inspect = useCallback((next: string[]) => { setIds(next); setOpen(next.length > 0); }, []);
  return <ActivityInspection.Provider value={inspect}>
    {children}
    <Modal open={open} onClose={() => setOpen(false)} title={t("hookFlow.executionDetails")} width="max-w-4xl" placement="right">
      <Suspense fallback={<LoadingBlock />}><ActivityDetailsDialog ids={ids} onReveal={(id) => setIds([id])} /></Suspense>
    </Modal>
  </ActivityInspection.Provider>;
}
