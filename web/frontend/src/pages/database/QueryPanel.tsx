import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation } from "@tanstack/react-query";
import CodeMirror from "@uiw/react-codemirror";
import { databaseApi } from "@/lib/api";
import type { DbQueryResult } from "@/lib/types";
import { useAppStore } from "@/stores/app-store";
import { Button, toast } from "@/components/ui";
import { Play, ShieldCheck } from "lucide-react";
import { CellContent } from "./CellContent";

export function QueryPanel({ db }: { db: string }) {
  const { t } = useTranslation("data");
  const theme = useAppStore((s) => s.theme);
  const [sql, setSql] = useState("");

  const queryMut = useMutation({
    mutationFn: () => databaseApi.query(db, sql).then((r) => r.data as DbQueryResult),
    onError: (e) => toast.error(`${t("db.queryFailed")}: ${e}`),
  });

  const result = queryMut.data;

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-[11px] text-muted">
        <ShieldCheck size={13} className="text-ok" />
        {t("db.queryReadonlyHint")}
      </div>
      <CodeMirror
        value={sql}
        onChange={setSql}
        theme={theme}
        height="120px"
        placeholder="SELECT * FROM memories LIMIT 50"
        style={{ fontSize: 13, borderRadius: 6, overflow: "hidden" }}
        basicSetup={{ lineNumbers: true, highlightActiveLine: true }}
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && sql.trim()) {
            queryMut.mutate();
          }
        }}
      />
      <div className="flex items-center gap-3">
        <Button
          variant="primary"
          size="sm"
          onClick={() => queryMut.mutate()}
          disabled={!sql.trim()}
          loading={queryMut.isPending}
        >
          <Play size={14} /> {t("db.runQuery")}
        </Button>
        {result && (
          <span className="text-xs text-muted">
            {t("db.queryStats", { count: result.row_count, ms: result.elapsed_ms })}
            {result.truncated && ` · ${t("db.queryTruncated")}`}
          </span>
        )}
      </div>

      {result && (
        result.rows.length === 0 ? (
          <p className="text-sm text-muted text-center py-6">{t("db.noRows")}</p>
        ) : (
          <div className="rounded-md border border-border overflow-auto max-h-[45vh]">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-panel z-10">
                <tr className="border-b border-border">
                  {result.columns.map((c) => (
                    <th key={c} className="px-2.5 py-2 text-left font-medium text-muted whitespace-nowrap">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.rows.map((row, i) => (
                  <tr key={i} className="border-b border-border/50 hover:bg-hover/50">
                    {result.columns.map((c) => (
                      <td key={c} className="px-2.5 py-1.5 max-w-[220px] truncate align-top">
                        <CellContent value={row[c] ?? null} />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}
    </div>
  );
}
