import { workspaceApi } from "@/lib/api";

interface PdfPreviewProps {
  path: string;
  title: string;
}

/** PDF 预览：浏览器原生阅读器（inline 响应，内联渲染而非触发下载） */
export function PdfPreview({ path, title }: PdfPreviewProps) {
  return (
    <iframe
      src={workspaceApi.rawUrl(path, true)}
      title={title}
      className="w-full h-full rounded-md border border-border bg-white"
    />
  );
}
