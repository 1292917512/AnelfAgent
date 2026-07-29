import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { storageApi } from "@/lib/api";
import { Button, Input, LoadingBlock, toast } from "@/components/ui";
import { AlertTriangle, CheckCircle2, FolderSearch, HardDrive, Rocket, XCircle } from "lucide-react";
import { formatSize } from "./format";

/** 存储位置：当前数据目录概览 + 目标校验 + 在线迁移向导 */
export function StoragePanel() {
  const { t } = useTranslation("data");
  const queryClient = useQueryClient();
  const [target, setTarget] = useState("");

  const { data: location, isLoading } = useQuery({
    queryKey: ["dbLocation"],
    queryFn: () => storageApi.location().then((r) => r.data),
  });

  const checkMut = useMutation({
    mutationFn: (value: string) => storageApi.checkTarget(value).then((r) => r.data),
  });

  const migrateMut = useMutation({
    mutationFn: (value: string) => storageApi.migrate(value).then((r) => r.data),
    onError: (e: unknown) => {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail || t("storage.migrateFailed"));
    },
  });

  const { data: migration } = useQuery({
    queryKey: ["dbMigration"],
    queryFn: () => storageApi.migrationStatus().then((r) => r.data),
    refetchInterval: (query) => (query.state.data?.state === "running" ? 1000 : false),
  });

  if (isLoading || !location) return <LoadingBlock label={t("common:loading")} />;

  const running = migration?.state === "running";
  const check = checkMut.data;
  const envBlocked = location.source === "env";
  const progress = migration && migration.total > 0 ? migration.done / migration.total : 0;

  return (
    <div className="space-y-4 max-w-3xl">
      {/* 当前位置 */}
      <div className="rounded-md border border-border bg-card p-4 space-y-3">
        <div className="flex items-center gap-2">
          <HardDrive size={15} className="text-accent" />
          <span className="text-sm font-semibold text-heading">{t("storage.current")}</span>
          <span className="ml-auto text-xs text-muted">
            {t(`storage.source.${location.source}`)} · {formatSize(location.total_bytes)}
          </span>
        </div>
        <p className="text-xs font-mono text-foreground break-all">{location.path}</p>
        {location.entries.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {location.entries.map((e) => (
              <span key={e.name} className="rounded bg-elevated px-1.5 py-0.5 text-[11px] font-mono text-muted">
                {e.name} <span className="text-foreground">{formatSize(e.size_bytes)}</span>
              </span>
            ))}
          </div>
        )}
      </div>

      {envBlocked && (
        <div className="flex items-start gap-2 rounded-md border border-warning/40 bg-warning/10 p-3 text-xs text-warning">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <span>{t("storage.envWarning", { value: location.env_override })}</span>
        </div>
      )}

      {migration?.state === "done" && (
        <div className="flex items-start gap-2 rounded-md border border-success/40 bg-success/10 p-3 text-xs text-success">
          <CheckCircle2 size={14} className="mt-0.5 shrink-0" />
          <span>{t("storage.restartHint", { target: migration.target })}</span>
        </div>
      )}
      {migration?.state === "error" && (
        <div className="flex items-start gap-2 rounded-md border border-danger/40 bg-danger/10 p-3 text-xs text-danger">
          <XCircle size={14} className="mt-0.5 shrink-0" />
          <span>{t("storage.errorHint", { error: migration.error })}</span>
        </div>
      )}

      {/* 迁移向导 */}
      <div className="rounded-md border border-border bg-card p-4 space-y-3">
        <span className="text-sm font-semibold text-heading flex items-center gap-2">
          <Rocket size={15} className="text-accent" /> {t("storage.migrateTitle")}
        </span>
        <div className="flex gap-2">
          <Input
            value={target}
            disabled={running || envBlocked}
            onChange={(e) => {
              setTarget(e.target.value);
              checkMut.reset();
            }}
            placeholder={t("storage.targetPlaceholder")}
            className="flex-1 font-mono"
          />
          <Button
            variant="secondary"
            disabled={!target.trim() || running || envBlocked}
            loading={checkMut.isPending}
            onClick={() => checkMut.mutate(target.trim())}
          >
            <FolderSearch size={14} /> {t("storage.check")}
          </Button>
        </div>

        {check && (
          <div className="space-y-1.5 text-xs">
            {check.ok ? (
              <p className="flex items-center gap-1.5 text-success">
                <CheckCircle2 size={13} />
                {t("storage.checkOk", { size: formatSize(check.required_bytes) })}
              </p>
            ) : (
              check.problems.map((p) => (
                <p key={p} className="flex items-center gap-1.5 text-danger">
                  <XCircle size={13} /> {t(`storage.problem.${p}`)}
                </p>
              ))
            )}
            {check.warnings.map((w) => (
              <p key={w} className="flex items-center gap-1.5 text-warning">
                <AlertTriangle size={13} /> {t(`storage.warning.${w}`)}
              </p>
            ))}
          </div>
        )}

        {running && migration && (
          <div className="space-y-1.5">
            <div className="h-1.5 rounded-full bg-elevated overflow-hidden">
              <div
                className="h-full bg-accent transition-all"
                style={{ width: `${Math.round(progress * 100)}%` }}
              />
            </div>
            <p className="text-[11px] text-muted font-mono truncate">
              {t("storage.running", { done: migration.done, total: migration.total })}
              {migration.current_file && ` · ${migration.current_file}`}
            </p>
          </div>
        )}

        <div className="flex items-center gap-3">
          <Button
            disabled={!check?.ok || running || envBlocked}
            loading={migrateMut.isPending || running}
            onClick={() => {
              migrateMut.mutate(target.trim(), {
                onSuccess: () => queryClient.invalidateQueries({ queryKey: ["dbMigration"] }),
              });
            }}
          >
            <Rocket size={14} /> {t("storage.migrate")}
          </Button>
          <p className="text-[11px] text-muted leading-snug">{t("storage.migrateHint")}</p>
        </div>
      </div>
    </div>
  );
}
