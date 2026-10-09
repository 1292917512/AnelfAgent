import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { tasksApi } from "@/lib/api";
import type { TaskConfig } from "@/lib/types";
import { useDraft } from "@/hooks/useDraft";
import { useUnsavedChanges } from "@/components/common/UnsavedChanges";
import { Drawer } from "@/components/common/Drawer";
import { Button, ConfirmDialog } from "@/components/ui";
import { EMPTY_TASK, TaskFormFields } from "./TaskForm";

export function taskIdentity(task: Pick<TaskConfig, "name" | "folder">): string {
  return JSON.stringify([task.folder ?? "", task.name]);
}

export function TaskEditor({ task, onClose }: { task: TaskConfig | null; onClose: () => void }) {
  const { t } = useTranslation(["workbench", "common"]);
  const client = useQueryClient();
  const draft = useDraft(task ?? EMPTY_TASK);
  const [confirmClose, setConfirmClose] = useState(false);
  useUnsavedChanges(draft.dirty);
  const save = useMutation({
    mutationFn: async (values: TaskConfig) => {
      const payload = { ...values, name: values.name.trim(), display_name: values.display_name.trim() || values.name.trim(),
        source: values.source || values.name.trim(), folder: (values.folder ?? "").trim().replace(/^\/+|\/+$/g, "") };
      return task ? tasksApi.update(task.name, payload, task.folder ?? "") : tasksApi.create(payload);
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["tasks"] });
      void client.invalidateQueries({ queryKey: ["heartbeatStatus"] });
      onClose();
    },
  });
  const close = () => { if (save.isPending) return; if (draft.dirty) setConfirmClose(true); else onClose(); };
  return <>
    <Drawer open onClose={close} dismissible={!save.isPending} width="max-w-2xl"
      title={task ? t("tasks.editTitle", { name: task.name }) : t("tasks.createTitle")}
      footer={<>
        <Button onClick={close} disabled={save.isPending}>{t("common:cancel")}</Button>
        <Button variant="primary" loading={save.isPending} disabled={!draft.dirty || !draft.values.name.trim() || !draft.values.prompt.trim()}
          onClick={() => save.mutate(draft.values)}>{t("common:save")}</Button>
      </>}>
      <fieldset disabled={save.isPending} className="space-y-5">
        <TaskFormFields task={draft.values} set={draft.update} isCreate={!task} />
      </fieldset>
    </Drawer>
    <ConfirmDialog open={confirmClose} title={t("common:unsavedChanges")} message={t("common:unsavedDescription")}
      confirmText={t("common:discardChanges")} cancelText={t("common:keepEditing")}
      onClose={() => setConfirmClose(false)} onConfirm={onClose} danger />
  </>;
}
