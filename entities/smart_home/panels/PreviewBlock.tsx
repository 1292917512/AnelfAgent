/** AI 注入预览块 — 默认按「域标签 + 条目」结构化展示，可切原文核对。
 *
 * 原文格式为 render_context 契约：首行 [智能家居]，其后每域一行
 * 「标签: 条目 | 条目」；解析失败的回退为整段原文。
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Braces, Eye, List, RefreshCw } from "lucide-react";
import { Card } from "@/components/common/Card";
import { Button, EmptyState } from "@/components/ui";
import type { PreviewResponse } from "./types";

interface PreviewBlockProps {
  preview?: PreviewResponse;
  onRefresh: () => void;
}

interface PreviewLine {
  label: string;
  items: string[];
}

function parseLines(content: string): PreviewLine[] | null {
  const lines = content.split("\n").filter((l) => l.trim());
  if (lines.length === 0) return null;
  const first = lines[0] ?? "";
  const body = first.startsWith("[") ? lines.slice(1) : lines;
  const parsed: PreviewLine[] = [];
  for (const line of body) {
    const cut = line.indexOf(": ");
    if (cut <= 0) return null;
    parsed.push({
      label: line.slice(0, cut),
      items: line.slice(cut + 2).split(" | ").filter(Boolean),
    });
  }
  return parsed;
}

export function PreviewBlock({ preview, onRefresh }: PreviewBlockProps) {
  const { t } = useTranslation("smart_home");
  const [raw, setRaw] = useState(false);
  const content = preview?.content ?? "";
  const lines = parseLines(content);

  return (
    <Card
      title={t("preview.title")}
      subtitle={t("preview.subtitle")}
      actions={
        <>
          <Button
            variant="ghost" size="sm"
            onClick={() => setRaw((v) => !v)}
            title={raw ? t("preview.structured") : t("preview.raw")}
          >
            {raw ? <List className="h-3.5 w-3.5 mr-1" /> : <Braces className="h-3.5 w-3.5 mr-1" />}
            {raw ? t("preview.structured") : t("preview.raw")}
          </Button>
          <Button variant="ghost" size="icon" onClick={onRefresh} title={t("preview.refresh")}>
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
        </>
      }
    >
      {!preview?.injecting || !content ? (
        <EmptyState icon={Eye} title={t("preview.empty")} className="py-8" />
      ) : raw || lines === null ? (
        <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-all rounded-md border border-border bg-elevated px-3 py-2 font-mono text-[12px] leading-relaxed text-foreground">
          {content}
        </pre>
      ) : (
        <div className="space-y-3">
          {lines.map((line) => (
            <section key={line.label}>
              <div className="mb-1.5 text-[12px] font-semibold text-muted-strong">
                {line.label}
              </div>
              <div className="flex flex-wrap gap-1.5">
                {line.items.map((item, i) => (
                  <span
                    key={`${line.label}-${i}`}
                    className="rounded-md border border-border bg-elevated px-2 py-1 font-mono text-[11px] leading-snug text-foreground"
                  >
                    {item}
                  </span>
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </Card>
  );
}
