import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { apiErrorMessage, mcpApi } from "@/lib/api";
import type { MCPServerConfig, MCPTransport } from "@/lib/types";
import { Button, Input, Modal, Select, Switch, Textarea, toast } from "@/components/ui";
import {
  inferTransport,
  parseArgsText,
  recordToRows,
  rowsToRecord,
  type KVRow,
} from "./types";

interface ServerFormModalProps {
  open: boolean;
  onClose: () => void;
  /** 编辑模式传入；为空则为添加模式 */
  editing?: { name: string; config: MCPServerConfig } | null;
}

function KVEditor({
  rows,
  onChange,
  addLabel,
}: {
  rows: KVRow[];
  onChange: (rows: KVRow[]) => void;
  addLabel: string;
}) {
  const { t } = useTranslation("mcp");
  return (
    <div className="space-y-1.5">
      {rows.map((row, i) => (
        <div key={i} className="flex items-center gap-1.5">
          <Input
            value={row.k}
            placeholder={t("form.kvKey")}
            className="flex-1 font-mono text-xs"
            onChange={(e) =>
              onChange(rows.map((r, j) => (j === i ? { ...r, k: e.target.value } : r)))
            }
          />
          <Input
            value={row.v}
            placeholder={t("form.kvValue")}
            className="flex-[2] font-mono text-xs"
            onChange={(e) =>
              onChange(rows.map((r, j) => (j === i ? { ...r, v: e.target.value } : r)))
            }
          />
          <Button
            variant="ghost"
            size="icon"
            onClick={() => onChange(rows.filter((_, j) => j !== i))}
          >
            <Trash2 size={14} />
          </Button>
        </div>
      ))}
      <Button
        variant="ghost"
        size="sm"
        onClick={() => onChange([...rows, { k: "", v: "" }])}
      >
        <Plus size={13} />
        {addLabel}
      </Button>
    </div>
  );
}

function Field({
  label,
  optional,
  children,
}: {
  label: string;
  optional?: boolean;
  children: React.ReactNode;
}) {
  const { t } = useTranslation("mcp");
  return (
    <label className="block space-y-1">
      <span className="text-xs font-medium text-muted">
        {label}
        {optional && (
          <span className="ml-1 text-[10px] text-muted/70">({t("form.optional")})</span>
        )}
      </span>
      {children}
    </label>
  );
}

/** 添加 / 编辑 MCP 服务器共用表单弹窗 */
export function ServerFormModal({ open, onClose, editing }: ServerFormModalProps) {
  const { t } = useTranslation("mcp");
  const queryClient = useQueryClient();
  const isEdit = !!editing;

  const [name, setName] = useState("");
  const [transport, setTransport] = useState<MCPTransport>("streamable_http");
  const [url, setUrl] = useState("");
  const [command, setCommand] = useState("");
  const [argsText, setArgsText] = useState("");
  const [envRows, setEnvRows] = useState<KVRow[]>([]);
  const [headerRows, setHeaderRows] = useState<KVRow[]>([]);
  const [timeout, setTimeout_] = useState("");
  const [sseReadTimeout, setSseReadTimeout] = useState("");
  const [callTimeout, setCallTimeout] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [formError, setFormError] = useState("");

  // 打开时按编辑/添加模式初始化表单
  useEffect(() => {
    if (!open) return;
    const cfg = editing?.config ?? {};
    setName(editing?.name ?? "");
    setTransport(inferTransport(cfg));
    setUrl(cfg.url ?? "");
    setCommand(cfg.command ?? "");
    setArgsText((cfg.args ?? []).join("\n"));
    setEnvRows(recordToRows(cfg.env));
    setHeaderRows(recordToRows(cfg.headers));
    setTimeout_(cfg.timeout != null ? String(cfg.timeout) : "");
    setSseReadTimeout(cfg.sse_read_timeout != null ? String(cfg.sse_read_timeout) : "");
    setCallTimeout(cfg.call_timeout != null ? String(cfg.call_timeout) : "");
    setEnabled(cfg.enabled ?? true);
    setFormError("");
  }, [open, editing]);

  const saveMutation = useMutation({
    mutationFn: ({ target, config }: { target: string; config: MCPServerConfig }) =>
      isEdit ? mcpApi.update(target, config) : mcpApi.add(target, config),
    onSuccess: (_r, { target }) => {
      queryClient.invalidateQueries({ queryKey: ["mcpServers"] });
      toast.success(t(isEdit ? "toast.serverUpdated" : "toast.serverAdded", { name: target }));
      onClose();
    },
    onError: (err) => {
      toast.error(apiErrorMessage(err, t("toast.requestFailed")));
    },
  });

  const parseTimeout = (raw: string): number | undefined | null => {
    const text = raw.trim();
    if (!text) return undefined;
    const num = Number(text);
    if (!Number.isFinite(num) || num <= 0) return null;
    return num;
  };

  const handleSubmit = () => {
    const target = name.trim();
    if (!isEdit && !target) {
      setFormError(t("form.nameRequired"));
      return;
    }
    const isStdio = transport === "stdio";
    if (isStdio && !command.trim()) {
      setFormError(t("form.urlOrCommandRequired"));
      return;
    }
    if (!isStdio && !url.trim()) {
      setFormError(t("form.urlOrCommandRequired"));
      return;
    }
    const timeouts = [parseTimeout(timeout), parseTimeout(sseReadTimeout), parseTimeout(callTimeout)];
    if (timeouts.some((v) => v === null)) {
      setFormError(t("form.timeoutPositive"));
      return;
    }

    const config: MCPServerConfig = { transport, enabled };
    if (isStdio) {
      config.command = command.trim();
      config.args = parseArgsText(argsText);
      const env = rowsToRecord(envRows);
      if (env) config.env = env;
    } else {
      config.url = url.trim();
      const headers = rowsToRecord(headerRows);
      if (headers) config.headers = headers;
    }
    if (timeouts[0]) config.timeout = timeouts[0];
    if (transport === "sse" && timeouts[1]) config.sse_read_timeout = timeouts[1];
    if (timeouts[2]) config.call_timeout = timeouts[2];

    setFormError("");
    saveMutation.mutate({ target: isEdit ? editing!.name : target, config });
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t(isEdit ? "editServer" : "addServer")}
      width="max-w-xl"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose}>
            {t("common:cancel")}
          </Button>
          <Button
            variant="primary"
            size="sm"
            loading={saveMutation.isPending}
            onClick={handleSubmit}
          >
            {t("common:save")}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label={t("form.name")}>
          <Input
            value={name}
            placeholder={t("form.namePlaceholder")}
            disabled={isEdit}
            onChange={(e) => setName(e.target.value)}
          />
          {isEdit && (
            <p className="text-[10px] text-muted">{t("form.nameHint")}</p>
          )}
        </Field>

        <Field label={t("form.transport")}>
          <Select
            className="w-full"
            value={transport}
            onChange={(e) => setTransport(e.target.value as MCPTransport)}
          >
            <option value="streamable_http">{t("form.transportHttp")}</option>
            <option value="sse">{t("form.transportSse")}</option>
            <option value="stdio">{t("form.transportStdio")}</option>
          </Select>
        </Field>

        {transport === "stdio" ? (
          <>
            <Field label={t("form.command")}>
              <Input
                value={command}
                placeholder={t("form.commandPlaceholder")}
                className="font-mono text-xs"
                onChange={(e) => setCommand(e.target.value)}
              />
            </Field>
            <Field label={t("form.args")} optional>
              <Textarea
                value={argsText}
                placeholder={t("form.argsPlaceholder")}
                rows={3}
                className="font-mono text-xs"
                onChange={(e) => setArgsText(e.target.value)}
              />
            </Field>
            <Field label={t("form.env")} optional>
              <KVEditor rows={envRows} onChange={setEnvRows} addLabel={t("form.addRow")} />
            </Field>
          </>
        ) : (
          <>
            <Field label={t("form.url")}>
              <Input
                value={url}
                placeholder={t("form.urlPlaceholder")}
                className="font-mono text-xs"
                onChange={(e) => setUrl(e.target.value)}
              />
            </Field>
            <Field label={t("form.headers")} optional>
              <KVEditor rows={headerRows} onChange={setHeaderRows} addLabel={t("form.addRow")} />
            </Field>
          </>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <Field label={t("form.timeout")} optional>
            <Input
              value={timeout}
              type="number"
              min={1}
              onChange={(e) => setTimeout_(e.target.value)}
            />
          </Field>
          {transport === "sse" && (
            <Field label={t("form.sseReadTimeout")} optional>
              <Input
                value={sseReadTimeout}
                type="number"
                min={1}
                onChange={(e) => setSseReadTimeout(e.target.value)}
              />
            </Field>
          )}
          <Field label={t("form.callTimeout")} optional>
            <Input
              value={callTimeout}
              type="number"
              min={1}
              onChange={(e) => setCallTimeout(e.target.value)}
            />
          </Field>
        </div>

        {!isEdit && (
          <label className="flex items-center gap-2 text-sm text-foreground">
            <Switch checked={enabled} onChange={setEnabled} />
            {t("form.enabledLabel")}
          </label>
        )}

        {formError && <p className="text-xs text-danger">{formError}</p>}
      </div>
    </Modal>
  );
}
