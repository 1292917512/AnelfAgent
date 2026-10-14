import type { ReactNode } from "react";
import { BookOpen, FileText, Folder, GraduationCap } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { fileReferenceKind, type FileReference } from "@/lib/file-reference";
import { useWorkbenchStore } from "@/stores/workbench-store";

export function FileReferenceLink({ reference, children }: { reference: FileReference; children: ReactNode }) {
  const { t } = useTranslation("chat");
  const navigate = useNavigate();
  const { path, root, isDir } = reference;
  const label = t(root === "project" ? "rootProject" : "rootWorkspace");
  const kind = fileReferenceKind(reference, typeof children === "string" ? children : undefined);
  const kindLabel = kind !== "file" ? t(`reference${kind === "skill" ? "Skill" : "Memory"}`) : "";
  const Icon = isDir ? Folder : kind === "skill" ? GraduationCap : kind === "memory" ? BookOpen : FileText;
  return <button type="button" title={`${kindLabel ? `${kindLabel} · ` : ""}${label} / ${path}`} onClick={() => {
    const state = useWorkbenchStore.getState();
    if (isDir) state.showSurface("files");
    if (!isDir) state.openFile(path, root);
    state.setFileTreeFocus(path, root);
    navigate("/");
  }} className="file-reference" data-kind={kind}>
    <Icon size={14} />
    <span className="truncate">{children}</span>
    <span className="file-reference-root">{kindLabel || label}</span>
  </button>;
}
