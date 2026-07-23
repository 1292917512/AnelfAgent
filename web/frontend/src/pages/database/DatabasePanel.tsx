import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { databaseApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { LoadingBlock } from "@/components/ui";
import {
  Database,
  Eye,
  FileCode2,
  HardDrive,
  Lock,
  Table2,
  TerminalSquare,
} from "lucide-react";
import { RowsPanel } from "./RowsPanel";
import { SchemaPanel } from "./SchemaPanel";
import { QueryPanel } from "./QueryPanel";

type SubTab = "rows" | "schema" | "query";

function formatSize(bytes: number): string {
  if (bytes >= 1 << 20) return `${(bytes / (1 << 20)).toFixed(1)} MB`;
  if (bytes >= 1 << 10) return `${(bytes / (1 << 10)).toFixed(1)} KB`;
  return `${bytes} B`;
}

export function DatabasePanel() {
  const { t } = useTranslation("data");
  const [dbId, setDbId] = useState<string | null>(null);
  const [table, setTable] = useState<string | null>(null);
  const [subTab, setSubTab] = useState<SubTab>("rows");
  const [includeShadow, setIncludeShadow] = useState(false);

  const { data: databases, isLoading: dbsLoading } = useQuery({
    queryKey: ["dbDatabases"],
    queryFn: () => databaseApi.databases().then((r) => r.data.items),
  });

  const activeDb = dbId ?? databases?.find((d) => d.exists)?.id ?? null;

  const { data: tables, isLoading: tablesLoading } = useQuery({
    queryKey: ["dbTables", activeDb, includeShadow],
    queryFn: () => databaseApi.tables(activeDb!, includeShadow).then((r) => r.data.items),
    enabled: !!activeDb,
  });

  const SUB_TABS: TabItem<SubTab>[] = [
    { key: "rows", label: t("db.tabRows"), icon: Eye },
    { key: "schema", label: t("db.tabSchema"), icon: FileCode2 },
    { key: "query", label: t("db.tabQuery"), icon: TerminalSquare },
  ];

  if (dbsLoading) return <LoadingBlock label={t("common:loading")} />;

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-4">
      {/* 左栏：库列表 + 表列表 */}
      <div className="space-y-3">
        <div className="space-y-2">
          {(databases ?? []).map((d) => (
            <button
              key={d.id}
              disabled={!d.exists}
              onClick={() => { setDbId(d.id); setTable(null); }}
              className={cn(
                "w-full text-left rounded-md border p-3 transition-all",
                activeDb === d.id
                  ? "border-accent bg-accent-subtle"
                  : "border-border bg-card hover:border-border-strong",
                !d.exists && "opacity-50 cursor-not-allowed",
              )}
            >
              <div className="flex items-center gap-2">
                <Database size={15} className={activeDb === d.id ? "text-accent" : "text-muted"} />
                <span className="text-sm font-medium text-heading">{d.name}</span>
                {d.exists ? (
                  <span className="ml-auto text-[10px] text-muted font-mono">
                    {formatSize(d.size_bytes)}
                  </span>
                ) : (
                  <span className="ml-auto text-[10px] text-muted">{t("db.notCreated")}</span>
                )}
              </div>
              <p className="text-[11px] text-muted mt-1 leading-snug">{d.description}</p>
              {d.exists && (
                <p className="text-[10px] text-muted/70 mt-1 font-mono truncate" title={d.path}>
                  {d.table_count} {t("db.tables")} · {d.path.split("/").pop()}
                </p>
              )}
            </button>
          ))}
        </div>

        {/* 表列表 */}
        {activeDb && (
          <div className="rounded-md border border-border bg-card">
            <div className="flex items-center justify-between px-3 py-2 border-b border-border">
              <span className="text-xs font-semibold text-muted uppercase tracking-wider flex items-center gap-1.5">
                <Table2 size={13} /> {t("db.tables")}
              </span>
              <label className="flex items-center gap-1 text-[10px] text-muted cursor-pointer">
                <input
                  type="checkbox"
                  checked={includeShadow}
                  onChange={(e) => setIncludeShadow(e.target.checked)}
                  className="accent-[var(--accent)]"
                />
                {t("db.showShadow")}
              </label>
            </div>
            <div className="max-h-[46vh] overflow-y-auto">
              {tablesLoading ? (
                <LoadingBlock label={t("common:loading")} />
              ) : !tables || tables.length === 0 ? (
                <p className="text-xs text-muted text-center py-4">{t("db.noTables")}</p>
              ) : (
                tables.map((tb) => (
                  <button
                    key={tb.name}
                    onClick={() => setTable(tb.name)}
                    className={cn(
                      "w-full flex items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors border-b border-border/40 last:border-0",
                      table === tb.name ? "bg-accent-subtle text-accent" : "text-foreground hover:bg-hover",
                    )}
                  >
                    <span className="font-mono truncate">{tb.name}</span>
                    {tb.readonly && <Lock size={10} className="text-muted shrink-0" />}
                    <span className="ml-auto text-[10px] text-muted shrink-0">
                      {tb.row_count >= 0 ? tb.row_count : "?"}
                    </span>
                  </button>
                ))
              )}
            </div>
          </div>
        )}
      </div>

      {/* 右侧内容区 */}
      <div className="min-w-0">
        {!activeDb ? (
          <div className="flex flex-col items-center justify-center py-16 text-muted">
            <HardDrive size={32} className="mb-2 opacity-40" />
            <p className="text-sm">{t("db.noDatabase")}</p>
          </div>
        ) : (
          <div className="space-y-3">
            <TabBar tabs={SUB_TABS} activeTab={subTab} onChange={setSubTab} />
            {subTab === "query" ? (
              <QueryPanel key={activeDb} db={activeDb} />
            ) : !table ? (
              <p className="text-sm text-muted text-center py-10">{t("db.selectTable")}</p>
            ) : subTab === "rows" ? (
              <RowsPanel key={`${activeDb}.${table}`} db={activeDb} table={table} />
            ) : (
              <SchemaPanel key={`${activeDb}.${table}`} db={activeDb} table={table} />
            )}
          </div>
        )}
      </div>
    </div>
  );
}
