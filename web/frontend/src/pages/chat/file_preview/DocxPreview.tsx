import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { workspaceApi } from "@/lib/api";
import { PreviewFrame, wrapPreviewDocument } from "./PreviewFrame";

interface DocxPreviewProps {
  path: string;
  title: string;
}

/** DOCX 预览：mammoth 转 HTML 后在禁脚本沙箱中渲染（库按需动态加载） */
export function DocxPreview({ path, title }: DocxPreviewProps) {
  const { t } = useTranslation("workbench");
  const [doc, setDoc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setDoc(null);
    setFailed(false);
    (async () => {
      try {
        const resp = await fetch(workspaceApi.rawUrl(path));
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const arrayBuffer = await resp.arrayBuffer();
        const mammoth = await import("mammoth");
        const result = await mammoth.convertToHtml({ arrayBuffer });
        if (!cancelled) setDoc(wrapPreviewDocument(result.value));
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => { cancelled = true; };
  }, [path]);

  if (failed) {
    return <p className="py-8 text-center text-sm text-danger">{t("editor.renderFailed")}</p>;
  }
  if (doc === null) {
    return (
      <div className="flex items-center gap-2 py-8 justify-center text-sm text-muted">
        <Loader2 size={16} className="animate-spin" /> {t("editor.loading")}
      </div>
    );
  }
  return <PreviewFrame doc={doc} title={title} />;
}
