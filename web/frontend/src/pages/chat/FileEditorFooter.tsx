import { useTranslation } from "react-i18next";
import { Loader2, Save } from "lucide-react";
import { Button } from "@/components/ui";
import type { TabState } from "./fileEditorUtils";

/** 文件编辑器底栏：路径/大小/保存状态 + 关闭/保存按钮 */
export function FileEditorFooter({
  cur,
  dirty,
  saving,
  savedTick,
  onClose,
  onSave,
}: {
  cur: TabState;
  dirty: boolean;
  saving: boolean;
  savedTick: boolean;
  onClose: () => void;
  onSave: () => void;
}) {
  const { t } = useTranslation("workbench");
  return (
    <div className="flex items-center gap-2 px-3 py-2 border-t border-border shrink-0">
      <span className="text-[11px] text-muted mr-auto truncate">
        {cur.file.path} · {(cur.file.size / 1024).toFixed(1)} KB
        {savedTick && <span className="text-ok ml-2">{t("editor.saved")}</span>}
      </span>
      <Button variant="secondary" size="sm" onClick={onClose}>
        {t("editor.close")}
      </Button>
      <Button
        variant="primary"
        size="sm"
        onClick={onSave}
        disabled={!dirty || saving || cur.file.binary || cur.file.truncated}
      >
        {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
        {t("editor.save")}
      </Button>
    </div>
  );
}
