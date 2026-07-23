import { PreviewFrame } from "./PreviewFrame";

interface HtmlPreviewProps {
  /** HTML 源文本（作为完整文档渲染，draft 变化时实时刷新） */
  html: string;
  title: string;
}

/** HTML 文件预览：脚本可运行但处于不透明源，无法访问主应用与同源存储 */
export function HtmlPreview({ html, title }: HtmlPreviewProps) {
  return <PreviewFrame doc={html} sandbox="allow-scripts" title={title} />;
}
