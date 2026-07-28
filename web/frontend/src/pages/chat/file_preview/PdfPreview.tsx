import { workspaceApi, type WorkspaceRoot } from "@/lib/api";

interface PdfPreviewProps {
  path: string;
  title: string;
  root?: WorkspaceRoot;
}

/** PDF 预览：浏览器原生阅读器（inline 响应，内联渲染而非触发下载） */
export function PdfPreview({ path, title, root = "workspace" }: PdfPreviewProps) {
  return (
    <iframe
      src={workspaceApi.rawUrl(path, true, root)}
      title={title}
      className="w-full h-full rounded-md border border-border bg-white"
    />
  );
}
