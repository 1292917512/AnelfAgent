import { useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { PrismLight as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import python from "react-syntax-highlighter/dist/esm/languages/prism/python";
import javascript from "react-syntax-highlighter/dist/esm/languages/prism/javascript";
import typescript from "react-syntax-highlighter/dist/esm/languages/prism/typescript";
import jsx from "react-syntax-highlighter/dist/esm/languages/prism/jsx";
import tsx from "react-syntax-highlighter/dist/esm/languages/prism/tsx";
import json from "react-syntax-highlighter/dist/esm/languages/prism/json";
import markdown from "react-syntax-highlighter/dist/esm/languages/prism/markdown";
import yaml from "react-syntax-highlighter/dist/esm/languages/prism/yaml";
import bash from "react-syntax-highlighter/dist/esm/languages/prism/bash";
import css from "react-syntax-highlighter/dist/esm/languages/prism/css";
import markup from "react-syntax-highlighter/dist/esm/languages/prism/markup";
import sql from "react-syntax-highlighter/dist/esm/languages/prism/sql";
import go from "react-syntax-highlighter/dist/esm/languages/prism/go";
import rust from "react-syntax-highlighter/dist/esm/languages/prism/rust";
import java from "react-syntax-highlighter/dist/esm/languages/prism/java";
import c from "react-syntax-highlighter/dist/esm/languages/prism/c";
import cpp from "react-syntax-highlighter/dist/esm/languages/prism/cpp";
import { Check, Copy, ExternalLink, Link2 } from "lucide-react";
import { useLightbox } from "./Lightbox";

SyntaxHighlighter.registerLanguage("python", python);
SyntaxHighlighter.registerLanguage("javascript", javascript);
SyntaxHighlighter.registerLanguage("js", javascript);
SyntaxHighlighter.registerLanguage("typescript", typescript);
SyntaxHighlighter.registerLanguage("ts", typescript);
SyntaxHighlighter.registerLanguage("jsx", jsx);
SyntaxHighlighter.registerLanguage("tsx", tsx);
SyntaxHighlighter.registerLanguage("json", json);
SyntaxHighlighter.registerLanguage("markdown", markdown);
SyntaxHighlighter.registerLanguage("md", markdown);
SyntaxHighlighter.registerLanguage("yaml", yaml);
SyntaxHighlighter.registerLanguage("yml", yaml);
SyntaxHighlighter.registerLanguage("bash", bash);
SyntaxHighlighter.registerLanguage("sh", bash);
SyntaxHighlighter.registerLanguage("shell", bash);
SyntaxHighlighter.registerLanguage("css", css);
SyntaxHighlighter.registerLanguage("html", markup);
SyntaxHighlighter.registerLanguage("xml", markup);
SyntaxHighlighter.registerLanguage("sql", sql);
SyntaxHighlighter.registerLanguage("go", go);
SyntaxHighlighter.registerLanguage("rust", rust);
SyntaxHighlighter.registerLanguage("java", java);
SyntaxHighlighter.registerLanguage("c", c);
SyntaxHighlighter.registerLanguage("cpp", cpp);

/** 从 URL 提取域名（失败返回空串） */
function domainOf(href: string): string {
  try {
    return new URL(href).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

/** 链接卡片：favicon + 域名 + 新窗口打开 */
function LinkCard({ href, children }: { href?: string; children?: ReactNode }) {
  const [faviconFailed, setFaviconFailed] = useState(false);
  const url = href || "";
  const domain = domainOf(url);
  const isExternal = /^https?:\/\//.test(url);

  if (!isExternal) {
    return (
      <a href={url} className="text-accent no-underline hover:underline">
        {children}
      </a>
    );
  }

  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer noopener"
      title={url}
      className="inline-flex items-center gap-1.5 max-w-full align-baseline px-1.5 py-0.5 mx-0.5 rounded-md bg-elevated border border-border text-accent no-underline hover:border-accent/50 hover:bg-accent-subtle transition-colors"
    >
      {faviconFailed || !domain ? (
        <Link2 size={12} className="shrink-0 text-muted" />
      ) : (
        <img
          src={`https://www.google.com/s2/favicons?domain=${domain}&sz=32`}
          alt=""
          loading="lazy"
          onError={() => setFaviconFailed(true)}
          className="w-3.5 h-3.5 rounded-sm shrink-0"
        />
      )}
      <span className="truncate">{children}</span>
      <ExternalLink size={11} className="shrink-0 opacity-60" />
    </a>
  );
}

/** 代码块：语法高亮 + 语言标签 + 复制按钮 */
function CodeBlock({ className, children }: { className?: string; children?: ReactNode }) {
  const [copied, setCopied] = useState(false);
  const match = /language-(\w+)/.exec(className || "");
  const lang = match?.[1] || "";
  const code = String(children ?? "").replace(/\n$/, "");

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch { /* 剪贴板不可用时忽略 */ }
  };

  return (
    <div className="rounded-md overflow-hidden border border-border my-2">
      <div className="flex items-center justify-between px-3 py-1.5 bg-elevated border-b border-border">
        <span className="text-[11px] font-mono text-muted">{lang || "text"}</span>
        <button
          onClick={copy}
          className="flex items-center gap-1 text-[11px] text-muted hover:text-foreground transition-colors"
        >
          {copied ? <Check size={12} className="text-ok" /> : <Copy size={12} />}
          {copied ? "OK" : "Copy"}
        </button>
      </div>
      <SyntaxHighlighter
        language={lang || "text"}
        style={oneDark}
        customStyle={{ margin: 0, borderRadius: 0, fontSize: 12.5, background: "var(--elevated)" }}
        codeTagProps={{ style: { fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" } }}
      >
        {code}
      </SyntaxHighlighter>
    </div>
  );
}

interface MarkdownProps {
  content: string;
}

/** 统一 Markdown 渲染：GFM + 代码高亮 + 链接卡片 + 图片灯箱 */
export function Markdown({ content }: MarkdownProps) {
  const { lightbox, openLightbox } = useLightbox();

  return (
    <div
      className="max-w-none break-words
        [&_p]:my-1 [&_ul]:my-1 [&_ol]:my-1 [&_li]:my-0.5
        [&_h1]:text-base [&_h1]:font-semibold [&_h1]:mt-3 [&_h1]:mb-1
        [&_h2]:text-[15px] [&_h2]:font-semibold [&_h2]:mt-3 [&_h2]:mb-1
        [&_h3]:text-sm [&_h3]:font-semibold [&_h3]:mt-2 [&_h3]:mb-1
        [&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3 [&_blockquote]:text-muted
        [&_table]:text-xs [&_th]:border [&_th]:border-border [&_th]:px-2 [&_th]:py-1
        [&_td]:border [&_td]:border-border [&_td]:px-2 [&_td]:py-1
        [&_code]:font-mono [&_code]:text-[12.5px]
        [&_:not(pre)>code]:bg-elevated [&_:not(pre)>code]:px-1 [&_:not(pre)>code]:py-0.5 [&_:not(pre)>code]:rounded
        [&_hr]:border-border [&_img]:rounded-md [&_img]:max-w-full"
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => <LinkCard href={href}>{children}</LinkCard>,
          pre: ({ children }) => {
            // react-markdown 将代码块渲染为 pre > code
            const child = Array.isArray(children) ? children[0] : children;
            if (child && typeof child === "object" && "props" in (child as Record<string, unknown>)) {
              const codeEl = child as { props: { className?: string; children?: ReactNode } };
              return <CodeBlock className={codeEl.props.className}>{codeEl.props.children}</CodeBlock>;
            }
            return <pre>{children}</pre>;
          },
          img: ({ src, alt }) => (
            <img
              src={src}
              alt={alt || ""}
              loading="lazy"
              onClick={() => typeof src === "string" && openLightbox(src, alt)}
              className="cursor-zoom-in hover:opacity-90 transition-opacity"
            />
          ),
        }}
      >
        {content}
      </ReactMarkdown>
      {lightbox}
    </div>
  );
}
