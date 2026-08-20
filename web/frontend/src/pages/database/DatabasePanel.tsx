import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { connectionApi, databaseApi } from "@/lib/api";
import type { DbConnection, DbInfo } from "@/lib/types";
import { cn } from "@/lib/utils";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { ConfirmDialog, LoadingBlock, toast } from "@/components/ui";
import {
  Database,
  Eye,
  FileCode2,
  HardDrive,
  Lock,
  Pencil,
  Plus,
  Table2,
  TerminalSquare,
  Trash2,
} from "lucide-react";
import { RowsPanel } from "./RowsPanel";
import { SchemaPanel } from "./SchemaPanel";
import { QueryPanel } from "./QueryPanel";
import { HealthCard } from "./HealthCard";
import { ConnectionDialog } from "./ConnectionDialog";
import { formatSize } from "./format";

type SubTab = "rows" | "schema" | "query";

export function DatabasePanel() {
  const { t } = useTranslation("data");
  const queryClient = useQueryClient();
  const [dbId, setDbId] = useState<string | null>(null);
  const [table, setTable] = useState<string | null>(null);
  const [subTab, setSubTab] = useState<SubTab>("rows");
  const [includeShadow, setIncludeShadow] = useState(false);
  const [connDialog, setConnDialog] = useState<{ open: boolean; conn: DbConnection | null }>({
    open: false,
    conn: null,
  });
  const [deleteConnTarget, setDeleteConnTarget] = useState<DbInfo | null>(null);

  const { data: databases, isLoading: dbsLoading } = useQuery({
    queryKey: ["dbDatabases"],
    queryFn: () => databaseApi.databases().then((r) => r.data.items),
    // cognee 存储统计为后台快照制，首次计算完成前返回空值，轮询兜底自愈
    refetchInterval: 30000,
  });

  const { data: connections } = useQuery({
    queryKey: ["dbConnections"],
    queryFn: () => connectionApi.list().then((r) => r.data.items),
  });

  const deleteConnMut = useMutation({
    mutationFn: (connId: string) => connectionApi.remove(connId),
    onSuccess: () => {
      toast.success(t("conn.deleted"));
      queryClient.invalidateQueries({ queryKey: ["dbConnections"] });
      queryClient.invalidateQueries({ queryKey: ["dbDatabases"] });
      setDeleteConnTarget(null);
    },
    onError: () => toast.error(t("conn.deleteFailed")),
  });

  const localDbs = (databases ?? []).filter((d) => !d.external);
  const extDbs = (databases ?? []).filter((d) => d.external);

  const activeDb = dbId ?? localDbs.find((d) => d.exists)?.id ?? null;
  const activeDbInfo = (databases ?? []).find((d) => d.id === activeDb) ?? null;
  const isExternal = activeDbInfo?.external ?? false;
  // cognee 为 lbug 引擎文件，非 SQLite，无健康概览
  const showHealth = !!activeDbInfo && !isExternal && activeDbInfo.exists && activeDb !== "cognee";

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

  const renderDbCard = (d: DbInfo) => {
    const connId = d.external ? d.id.slice(4) : null;
    const connMeta = connId ? connections?.find((c) => c.id === connId) : null;
    return (
      <div
        key={d.id}
        className={cn(
          "relative rounded-md border transition-all",
          activeDb === d.id
            ? "border-accent bg-accent-subtle"
            : "border-border bg-card hover:border-border-strong",
          !d.exists && "opacity-50",
        )}
      >
        <button
          disabled={!d.exists}
          onClick={() => { setDbId(d.id); setTable(null); }}
          className="w-full text-left p-3 disabled:cursor-not-allowed"
        >
          <div className="flex items-center gap-2">
            <Database size={15} className={activeDb === d.id ? "text-accent" : "text-muted"} />
            <span className="text-sm font-medium text-heading">{d.name}</span>
            {d.exists ? (
              <span className="ml-auto text-[10px] text-muted font-mono">
                {d.external ? (d.engine ?? "") : formatSize(d.size_bytes)}
              </span>
            ) : (
              <span className="ml-auto text-[10px] text-muted">
                {d.external ? t("conn.unreachable") : t("db.notCreated")}
              </span>
            )}
          </div>
          <p className="text-[11px] text-muted mt-1 leading-snug">{d.description}</p>
          {d.exists && !d.external && (
            <p className="text-[10px] text-muted/70 mt-1 font-mono truncate" title={d.path}>
              {d.table_count} {t("db.tables")} · {d.path.split("/").pop()}
            </p>
          )}
          {d.external && (
            <p className="text-[10px] text-muted/70 mt-1 font-mono">
              {d.table_count} {t("db.tables")} · {t("db.readonly")}
            </p>
          )}
        </button>
        {d.external && connMeta && (
          <div className="absolute top-2 right-2 flex gap-1">
            <button
              onClick={() => setConnDialog({ open: true, conn: connMeta })}
              className="p-1 rounded text-muted hover:text-accent transition-colors"
              title={t("common:edit")}
            >
              <Pencil size={12} />
            </button>
            <button
              onClick={() => setDeleteConnTarget(d)}
              className="p-1 rounded text-muted hover:text-danger transition-colors"
              title={t("common:delete")}
            >
              <Trash2 size={12} />
            </button>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-4">
      {/* 左栏：库列表 + 表列表 */}
      <div className="space-y-3">
        {/* 本地库 */}
        <div className="space-y-2">
          <span className="text-xs font-semibold text-muted uppercase tracking-wider px-1">
            {t("db.localGroup")}
          </span>
          {localDbs.map(renderDbCard)}
        </div>

        {/* 外部连接 */}
        <div className="space-y-2">
          <div className="flex items-center justify-between px-1">
            <span className="text-xs font-semibold text-muted uppercase tracking-wider">
              {t("db.externalGroup")}
            </span>
            <button
              onClick={() => setConnDialog({ open: true, conn: null })}
              className="flex items-center gap-1 text-[11px] text-muted hover:text-accent transition-colors"
            >
              <Plus size={12} /> {t("conn.add")}
            </button>
          </div>
          {extDbs.length === 0 ? (
            <p className="text-[11px] text-muted px-1">{t("conn.empty")}</p>
          ) : (
            extDbs.map(renderDbCard)
          )}
        </div>

        {/* 表列表 */}
        {activeDb && (
          <div className="rounded-md border border-border bg-card">
            <div className="flex items-center justify-between px-3 py-2 border-b border-border">
              <span className="text-xs font-semibold text-muted uppercase tracking-wider flex items-center gap-1.5">
                <Table2 size={13} /> {t("db.tables")}
              </span>
              {!isExternal && (
                <label className="flex items-center gap-1 text-[10px] text-muted cursor-pointer">
                  <input
                    type="checkbox"
                    checked={includeShadow}
                    onChange={(e) => setIncludeShadow(e.target.checked)}
                    className="accent-[var(--accent)]"
                  />
                  {t("db.showShadow")}
                </label>
              )}
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
            {showHealth && <HealthCard db={activeDb} />}
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

      <ConnectionDialog
        key={connDialog.conn?.id ?? "new"}
        open={connDialog.open}
        connection={connDialog.conn}
        onClose={() => setConnDialog({ open: false, conn: null })}
      />
      <ConfirmDialog
        open={deleteConnTarget !== null}
        onClose={() => setDeleteConnTarget(null)}
        onConfirm={() => deleteConnTarget && deleteConnMut.mutate(deleteConnTarget.id.slice(4))}
        title={t("common:delete")}
        message={t("conn.deleteConfirm", { name: deleteConnTarget?.name ?? "" })}
        danger
        loading={deleteConnMut.isPending}
      />
    </div>
  );
}
