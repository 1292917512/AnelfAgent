import type { ReactNode } from "react";
import { FileText, Folder } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import type { FileReference } from "@/lib/file-reference";
import { useWorkbenchStore } from "@/stores/workbench-store";

export function FileReferenceLink({ reference, children }: { reference: FileReference; children: ReactNode }) {
  const { t } = useTranslation("chat");
  const navigate = useNavigate();
  const { path, root, isDir } = reference;
  const label = t(root === "project" ? "rootProject" : "rootWorkspace");
  return <button type="button" title={`${label} / ${path}`} onClick={() => {
    const state = useWorkbenchStore.getState();
    if (isDir && !state.leftOpen) state.toggleLeft();
    if (!isDir) state.openFile(path, root);
    state.setFileTreeFocus(path, root);
    navigate("/");
  }} className="file-reference">
    {isDir ? <Folder size={14} /> : <FileText size={14} />}
    <span className="truncate">{children}</span>
    <span className="file-reference-root">{label}</span>
  </button>;
}
