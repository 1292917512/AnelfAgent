import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { workspaceApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import { PreviewFrame, wrapPreviewDocument } from "./PreviewFrame";

/** Sheet 表格的补充排版样式 */
const TABLE_CSS = `
  body { padding: 8px; }
  table { font-size: 12px; }
  td, th { padding: 3px 8px; white-space: nowrap; }
`;

/** 单个 Sheet 的渲染结果 */
interface SheetDoc {
  name: string;
  doc: string;
}

interface XlsxPreviewProps {
  path: string;
  title: string;
}

/** XLSX 预览：SheetJS 解析工作簿，Sheet 标签切换 + 禁脚本沙箱表格渲染（库按需动态加载） */
export function XlsxPreview({ path, title }: XlsxPreviewProps) {
  const { t } = useTranslation("workbench");
  const [sheets, setSheets] = useState<SheetDoc[] | null>(null);
  const [active, setActive] = useState(0);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setSheets(null);
    setActive(0);
    setFailed(false);
    (async () => {
      try {
        const resp = await fetch(workspaceApi.rawUrl(path));
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const buf = await resp.arrayBuffer();
        const XLSX = await import("xlsx");
        const wb = XLSX.read(buf, { type: "array" });
        const parsed: SheetDoc[] = [];
        for (const name of wb.SheetNames) {
          const sheet = wb.Sheets[name];
          if (!sheet) continue;
          parsed.push({ name, doc: wrapPreviewDocument(XLSX.utils.sheet_to_html(sheet), TABLE_CSS) });
        }
        if (parsed.length === 0) throw new Error("no sheets");
        if (!cancelled) setSheets(parsed);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => { cancelled = true; };
  }, [path]);

  if (failed) {
    return <p className="py-8 text-center text-sm text-danger">{t("editor.renderFailed")}</p>;
  }
  if (sheets === null) {
    return (
      <div className="flex items-center gap-2 py-8 justify-center text-sm text-muted">
        <Loader2 size={16} className="animate-spin" /> {t("editor.loading")}
      </div>
    );
  }

  const current = sheets[Math.min(active, sheets.length - 1)];
  return (
    <div className="flex-1 min-h-0 flex flex-col gap-1.5">
      {sheets.length > 1 && (
        <div className="flex items-center gap-1 overflow-x-auto shrink-0">
          {sheets.map((s, i) => (
            <button
              key={s.name}
              onClick={() => setActive(i)}
              className={cn(
                "px-2.5 py-1 rounded-md text-xs whitespace-nowrap transition-colors",
                i === active ? "bg-accent-subtle text-accent" : "text-muted hover:bg-hover hover:text-foreground",
              )}
            >
              {s.name}
            </button>
          ))}
        </div>
      )}
      {current && <PreviewFrame doc={current.doc} title={`${title} - ${current.name}`} />}
    </div>
  );
}
