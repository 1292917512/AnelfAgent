/** 预览文档的基础排版样式（注入 srcdoc，与主应用主题隔离，始终白底） */
const BASE_CSS = `
  body { margin: 0; padding: 12px 16px; font: 14px/1.6 -apple-system, "Segoe UI", Roboto, sans-serif; color: #1f2328; background: #fff; }
  img { max-width: 100%; height: auto; }
  table { border-collapse: collapse; }
  td, th { border: 1px solid #d0d7de; padding: 4px 8px; }
  pre, code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
`;

/** 将正文 HTML 包装为完整的预览文档 */
export function wrapPreviewDocument(bodyHtml: string, extraCss = ""): string {
  return `<!doctype html><html><head><meta charset="utf-8"><style>${BASE_CSS}${extraCss}</style></head><body>${bodyHtml}</body></html>`;
}

interface PreviewFrameProps {
  /** 完整 HTML 文档内容（srcdoc） */
  doc: string;
  /** iframe sandbox 权限标记，默认完全禁用脚本与同源（不透明源） */
  sandbox?: string;
  title: string;
}

/** 沙箱预览框架：不可信 HTML 在不透明源 iframe 中渲染，与主应用 DOM/Cookie 完全隔离 */
export function PreviewFrame({ doc, sandbox = "", title }: PreviewFrameProps) {
  return (
    <iframe
      srcDoc={doc}
      sandbox={sandbox}
      title={title}
      className="w-full h-full rounded-md border border-border bg-white"
    />
  );
}
