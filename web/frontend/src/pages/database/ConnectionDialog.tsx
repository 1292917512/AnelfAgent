import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { connectionApi } from "@/lib/api";
import type { DbConnection, DbConnectionPayload } from "@/lib/types";
import { Button, Input, Modal, Select, toast } from "@/components/ui";
import { PlugZap } from "lucide-react";

interface Props {
  open: boolean;
  onClose: () => void;
  /** 编辑既有连接时传入；新增为 null */
  connection: DbConnection | null;
}

/** 外部数据库连接编辑对话框（新增 / 编辑 / 测试连接） */
export function ConnectionDialog({ open, onClose, connection }: Props) {
  const { t } = useTranslation("data");
  const queryClient = useQueryClient();
  const editing = connection !== null;

  const [form, setForm] = useState<DbConnectionPayload>({
    name: connection?.name ?? "",
    engine: connection?.engine ?? "postgresql",
    host: connection?.host ?? "127.0.0.1",
    port: connection?.port ?? 0,
    database: connection?.database ?? "",
    username: connection?.username ?? "",
    password: "",
  });
  const [testResult, setTestResult] = useState<{ ok: boolean; text: string } | null>(null);

  const set = <K extends keyof DbConnectionPayload>(key: K, value: DbConnectionPayload[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const saveMut = useMutation({
    mutationFn: () =>
      editing
        ? connectionApi.update(connection.id, form)
        : connectionApi.create(form),
    onSuccess: () => {
      toast.success(t("conn.saved"));
      queryClient.invalidateQueries({ queryKey: ["dbConnections"] });
      queryClient.invalidateQueries({ queryKey: ["dbDatabases"] });
      onClose();
    },
    onError: (e: unknown) => {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail || t("conn.saveFailed"));
    },
  });

  const testMut = useMutation({
    mutationFn: () =>
      connectionApi.test(editing ? { ...form, id: connection.id } : form),
    onSuccess: (res) => {
      const d = res.data;
      setTestResult(
        d.ok
          ? { ok: true, text: `${t("conn.testOk")} · ${d.version ?? ""} · ${d.latency_ms}ms` }
          : { ok: false, text: `${t("conn.testFailed")}: ${d.error ?? ""}` },
      );
    },
    onError: (e: unknown) => {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setTestResult({ ok: false, text: `${t("conn.testFailed")}: ${detail ?? ""}` });
    },
  });

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={editing ? t("conn.edit") : t("conn.add")}
      footer={
        <div className="flex items-center gap-2">
          <Button variant="secondary" loading={testMut.isPending} onClick={() => testMut.mutate()}>
            <PlugZap size={14} /> {t("conn.test")}
          </Button>
          <div className="flex-1" />
          <Button variant="ghost" onClick={onClose}>{t("common:cancel")}</Button>
          <Button loading={saveMut.isPending} onClick={() => saveMut.mutate()}>
            {t("conn.save")}
          </Button>
        </div>
      }
    >
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <label className="space-y-1">
            <span className="text-xs text-muted">{t("conn.name")}</span>
            <Input value={form.name} onChange={(e) => set("name", e.target.value)} />
          </label>
          <label className="space-y-1">
            <span className="text-xs text-muted">{t("conn.engine")}</span>
            <Select
              className="w-full"
              value={form.engine}
              onChange={(e) => set("engine", e.target.value)}
            >
              <option value="postgresql">PostgreSQL</option>
              <option value="mysql">MySQL</option>
            </Select>
          </label>
        </div>
        <div className="grid grid-cols-[1fr_120px] gap-3">
          <label className="space-y-1">
            <span className="text-xs text-muted">{t("conn.host")}</span>
            <Input value={form.host} onChange={(e) => set("host", e.target.value)} />
          </label>
          <label className="space-y-1">
            <span className="text-xs text-muted">{t("conn.port")}</span>
            <Input
              type="number"
              value={form.port || ""}
              placeholder={form.engine === "mysql" ? "3306" : "5432"}
              onChange={(e) => set("port", Number(e.target.value) || 0)}
            />
          </label>
        </div>
        <label className="space-y-1 block">
          <span className="text-xs text-muted">{t("conn.database")}</span>
          <Input value={form.database} onChange={(e) => set("database", e.target.value)} />
        </label>
        <div className="grid grid-cols-2 gap-3">
          <label className="space-y-1">
            <span className="text-xs text-muted">{t("conn.username")}</span>
            <Input value={form.username} onChange={(e) => set("username", e.target.value)} />
          </label>
          <label className="space-y-1">
            <span className="text-xs text-muted">{t("conn.password")}</span>
            <Input
              type="password"
              value={form.password}
              placeholder={editing && connection.has_password ? "****" : ""}
              onChange={(e) => set("password", e.target.value)}
            />
          </label>
        </div>
        <p className="text-[11px] text-muted">{t("conn.passwordHint")}</p>
        {testResult && (
          <p className={`text-xs ${testResult.ok ? "text-success" : "text-danger"}`}>
            {testResult.text}
          </p>
        )}
      </div>
    </Modal>
  );
}
