import { createHighlighterCore, type HighlighterCore } from "shiki/core";
import { createJavaScriptRegexEngine } from "shiki/engine/javascript";

/**
 * Shiki 高亮服务（懒加载单例）：
 * - 使用 JS 正则引擎，避免加载/编译 oniguruma WASM（本地 serve 场景更稳、更快）
 * - 每种语言的 TextMate 语法独立成 chunk，首次用到才下载
 * - 双主题（github-light / one-dark-pro）一次高亮同时产出两套 CSS 变量，
 *   应用主题切换由 CSS 接管，无需重新高亮
 */

/** 支持的语言 → 按需动态加载语法模块 */
const LANG_LOADERS = {
  python: () => import("shiki/langs/python.mjs"),
  javascript: () => import("shiki/langs/javascript.mjs"),
  typescript: () => import("shiki/langs/typescript.mjs"),
  jsx: () => import("shiki/langs/jsx.mjs"),
  tsx: () => import("shiki/langs/tsx.mjs"),
  json: () => import("shiki/langs/json.mjs"),
  markdown: () => import("shiki/langs/markdown.mjs"),
  yaml: () => import("shiki/langs/yaml.mjs"),
  bash: () => import("shiki/langs/bash.mjs"),
  css: () => import("shiki/langs/css.mjs"),
  html: () => import("shiki/langs/html.mjs"),
  xml: () => import("shiki/langs/xml.mjs"),
  sql: () => import("shiki/langs/sql.mjs"),
  go: () => import("shiki/langs/go.mjs"),
  rust: () => import("shiki/langs/rust.mjs"),
  java: () => import("shiki/langs/java.mjs"),
  c: () => import("shiki/langs/c.mjs"),
  cpp: () => import("shiki/langs/cpp.mjs"),
} satisfies Record<string, () => Promise<{ default: unknown }>>;

/** 常见别名归一化；返回空串表示按纯文本处理 */
const LANG_ALIAS: Record<string, string> = {
  js: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  ts: "typescript",
  mts: "typescript",
  py: "python",
  sh: "bash",
  shell: "bash",
  zsh: "bash",
  yml: "yaml",
  md: "markdown",
  "c++": "cpp",
  vue: "html",
  plaintext: "",
  text: "",
  txt: "",
};

function normalizeLang(lang: string): string {
  const lower = lang.trim().toLowerCase();
  if (!lower) return "";
  const aliased = LANG_ALIAS[lower] ?? lower;
  return aliased in LANG_LOADERS ? aliased : "";
}

let highlighterPromise: Promise<HighlighterCore> | null = null;
const loadedLangs = new Set<string>();
const pendingLangs = new Map<string, Promise<boolean>>();

function getHighlighter(): Promise<HighlighterCore> {
  if (!highlighterPromise) {
    highlighterPromise = createHighlighterCore({
      themes: [
        import("shiki/themes/one-dark-pro.mjs"),
        import("shiki/themes/github-light.mjs"),
      ],
      langs: [],
      engine: createJavaScriptRegexEngine(),
    });
  }
  return highlighterPromise;
}

/**
 * 将代码高亮为 HTML 字符串；语言不受支持或失败时返回 null（调用方走纯文本兜底）。
 * 输出使用 --shiki-light / --shiki-dark CSS 变量，主题切换由 globals.css 控制。
 */
export async function highlightCode(
  code: string,
  lang: string,
): Promise<string | null> {
  const normalized = normalizeLang(lang);
  if (!normalized) return null;

  const loader = LANG_LOADERS[normalized as keyof typeof LANG_LOADERS];
  try {
    const highlighter = await getHighlighter();
    if (!loadedLangs.has(normalized)) {
      let pending = pendingLangs.get(normalized);
      if (!pending) {
        pending = loader()
          .then(async (mod) => {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            await highlighter.loadLanguage(mod.default as any);
            loadedLangs.add(normalized);
            return true;
          })
          .catch(() => false);
        pendingLangs.set(normalized, pending);
      }
      if (!(await pending)) return null;
    }
    return highlighter.codeToHtml(code, {
      lang: normalized,
      themes: { light: "github-light", dark: "one-dark-pro" },
      defaultColor: false,
    });
  } catch {
    return null;
  }
}
