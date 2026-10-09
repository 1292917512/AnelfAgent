import { createContext, useCallback, useContext, useEffect, useId, useState, type ReactNode } from "react";
import { useBeforeUnload, useBlocker } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ConfirmDialog } from "@/components/ui/Modal";

const UnsavedContext = createContext<((id: string, dirty: boolean) => void) | null>(null);

export function UnsavedChangesProvider({ children }: { children: ReactNode }) {
  const [drafts, setDrafts] = useState<Set<string>>(new Set());
  const register = useCallback((id: string, dirty: boolean) => {
    setDrafts((current) => {
      if (current.has(id) === dirty) return current;
      const next = new Set(current);
      if (dirty) next.add(id); else next.delete(id);
      return next;
    });
  }, []);
  const dirty = drafts.size > 0;
  const blocker = useBlocker(dirty);
  const { t } = useTranslation("common");
  useBeforeUnload(useCallback((event) => {
    if (dirty) { event.preventDefault(); event.returnValue = ""; }
  }, [dirty]));
  return (
    <UnsavedContext.Provider value={register}>
      {children}
      <ConfirmDialog open={blocker.state === "blocked"} title={t("unsavedChanges")} message={t("unsavedDescription")}
        confirmText={t("discardChanges")} cancelText={t("keepEditing")} danger
        onClose={() => blocker.reset?.()} onConfirm={() => blocker.proceed?.()} />
    </UnsavedContext.Provider>
  );
}

export function useUnsavedChanges(dirty: boolean): void {
  const register = useContext(UnsavedContext);
  const id = useId();
  if (!register) throw new Error("UnsavedChangesProvider is required");
  useEffect(() => {
    register(id, dirty);
    return () => register(id, false);
  }, [register, id, dirty]);
}
