import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  Copy,
  FileCode2,
  FileUp,
  KeyRound,
  Pencil,
  Plus,
  RefreshCw,
  Rocket,
  Trash2,
} from "lucide-react";
import CodeMirror from "@uiw/react-codemirror";
import { yaml as yamlLang } from "@codemirror/lang-yaml";
import { difyApi } from "./api";
import type { DifyApp } from "./types";
import { Card } from "@/components/common/Card";
import { Badge } from "@/components/ui";
import { Button, ConfirmDialog, Input, Modal, Select, Textarea, toast } from "@/components/ui";
import { apiErrorMessage } from "@/lib/api";

const APP_MODES = ["chat", "agent-chat", "advanced-chat", "workflow", "completion"] as const;

const WORKFLOW_MODES = new Set(["workflow", "advanced-chat"]);

export function AppsTab() {
  const { t } = useTranslation("dify");
  const queryClient = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [newMode, setNewMode] = useState<string>("chat");
  const [newDesc, setNewDesc] = useState("");
  const [importYaml, setImportYaml] = useState("");
  const [importName, setImportName] = useState("");
  const [dslApp, setDslApp] = useState<DifyApp | null>(null);
  const [dslText, setDslText] = useState("");
  const [dslLoading, setDslLoading] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<DifyApp | null>(null);

  const { data, refetch } = useQuery({
    queryKey: ["dify-apps"],
    queryFn: () => difyApi.listApps().then((r) => r.data),
    refetchInterval: 15000,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["dify-apps"] });
  const onError = (err: unknown) => toast.error(apiErrorMessage(err, t("messages.actionFailed")));

  const createMutation = useMutation({
    mutationFn: () => difyApi.createApp(newName.trim(), newMode, newDesc.trim()),
    onSuccess: (resp) => {
      toast.success(resp.data?.message || t("messages.actionDone"));
      setCreateOpen(false);
      setNewName("");
      setNewDesc("");
      invalidate();
    },
    onError,
  });

  const importMutation = useMutation({
    mutationFn: () => difyApi.importDsl(importYaml, importName.trim()),
    onSuccess: (resp) => {
      toast.success(resp.data?.message || t("messages.actionDone"));
      setImportOpen(false);
      setImportYaml("");
      setImportName("");
      invalidate();
    },
    onError,
  });

  const applyMutation = useMutation({
    mutationFn: (publish: boolean) =>
      difyApi.applyDsl(dslApp!.id, dslText, publish),
    onSuccess: (resp) => {
      toast.success(resp.data?.message || t("messages.actionDone"));
      invalidate();
    },
    onError,
  });

  const publishMutation = useMutation({
    mutationFn: (app: DifyApp) => difyApi.publish(app.id),
    onSuccess: (resp) => {
      toast.success(resp.data?.message || t("messages.actionDone"));
      invalidate();
    },
    onError,
  });

  const copyMutation = useMutation({
    mutationFn: (app: DifyApp) => difyApi.copyApp(app.id, `${app.name} copy`),
    onSuccess: () => {
      toast.success(t("messages.actionDone"));
      invalidate();
    },
    onError,
  });

  const keyMutation = useMutation({
    mutationFn: (app: DifyApp) => difyApi.createApiKey(app.id),
    onSuccess: (resp) => {
      toast.success(resp.data?.message || t("messages.actionDone"));
      invalidate();
    },
    onError,
  });

  const deleteMutation = useMutation({
    mutationFn: (app: DifyApp) => difyApi.deleteApp(app.id),
    onSuccess: () => {
      toast.success(t("messages.actionDone"));
      setDeleteTarget(null);
      invalidate();
    },
    onError,
  });

  const openDsl = async (app: DifyApp) => {
    setDslApp(app);
    setDslLoading(true);
    try {
      const resp = await difyApi.exportDsl(app.id);
      setDslText(resp.data.dsl || "");
    } catch (err) {
      toast.error(apiErrorMessage(err, t("messages.actionFailed")));
      setDslApp(null);
    } finally {
      setDslLoading(false);
    }
  };

  const apps = data?.apps ?? [];

  return (
    <div className="space-y-4 max-w-4xl">
      <Card
        title={t("apps.title")}
        subtitle={t("apps.count", { count: apps.length })}
        actions={
          <div className="flex gap-2">
            <button
              onClick={() => refetch()}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-elevated text-muted hover:bg-hover transition-all"
            >
              <RefreshCw size={14} /> {t("actions.refresh")}
            </button>
            <button
              onClick={() => setImportOpen(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-elevated text-muted hover:bg-hover transition-all"
            >
              <FileUp size={14} /> {t("apps.import")}
            </button>
            <button
              onClick={() => setCreateOpen(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md bg-accent text-white hover:opacity-90 transition-all"
            >
              <Plus size={14} /> {t("apps.create")}
            </button>
          </div>
        }
      >
        {apps.length === 0 ? (
          <div className="py-10 text-center text-sm text-muted">{t("apps.empty")}</div>
        ) : (
          <div className="space-y-2">
            {apps.map((app) => (
              <div
                key={app.id}
                className="flex items-center gap-3 p-3 rounded-lg border border-border bg-elevated hover:border-border-strong transition-all"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-heading truncate">{app.name}</span>
                    <Badge variant="info">{app.mode}</Badge>
                    {app.has_api_key && <Badge variant="ok">API</Badge>}
                    {app.mcp_server_code && <Badge variant="warn">MCP</Badge>}
                  </div>
                  <div className="text-xs text-muted truncate mt-0.5">
                    {app.id}
                    {app.description && ` · ${app.description}`}
                  </div>
                </div>
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => openDsl(app)}
                    title={t("apps.editDsl")}
                    className="p-1.5 rounded-md text-muted hover:text-accent hover:bg-hover transition-all"
                  >
                    <Pencil size={15} />
                  </button>
                  {WORKFLOW_MODES.has(app.mode) && (
                    <button
                      onClick={() => publishMutation.mutate(app)}
                      title={t("apps.publish")}
                      className="p-1.5 rounded-md text-muted hover:text-ok hover:bg-hover transition-all"
                    >
                      <Rocket size={15} />
                    </button>
                  )}
                  {!app.has_api_key && (
                    <button
                      onClick={() => keyMutation.mutate(app)}
                      title={t("apps.createKey")}
                      className="p-1.5 rounded-md text-muted hover:text-accent hover:bg-hover transition-all"
                    >
                      <KeyRound size={15} />
                    </button>
                  )}
                  <button
                    onClick={() => copyMutation.mutate(app)}
                    title={t("apps.copy")}
                    className="p-1.5 rounded-md text-muted hover:text-accent hover:bg-hover transition-all"
                  >
                    <Copy size={15} />
                  </button>
                  <button
                    onClick={() => setDeleteTarget(app)}
                    title={t("apps.delete")}
                    className="p-1.5 rounded-md text-muted hover:text-danger hover:bg-hover transition-all"
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* 新建应用 */}
      <Modal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title={t("apps.createTitle")}
        footer={
          <div className="flex justify-end gap-2">
            <Button variant="secondary" size="sm" onClick={() => setCreateOpen(false)}>
              {t("actions.cancel")}
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={() => createMutation.mutate()}
              loading={createMutation.isPending}
              disabled={!newName.trim()}
            >
              {t("actions.save")}
            </Button>
          </div>
        }
      >
        <div className="space-y-3">
          <div>
            <label className="block text-xs text-muted mb-1">{t("apps.fieldName")}</label>
            <Input value={newName} onChange={(e) => setNewName(e.target.value)} />
          </div>
          <div>
            <label className="block text-xs text-muted mb-1">{t("apps.fieldMode")}</label>
            <Select value={newMode} onChange={(e) => setNewMode(e.target.value)} className="w-full">
              {APP_MODES.map((m) => (
                <option key={m} value={m}>
                  {t(`apps.modes.${m}`)}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <label className="block text-xs text-muted mb-1">{t("apps.fieldDesc")}</label>
            <Input value={newDesc} onChange={(e) => setNewDesc(e.target.value)} />
          </div>
        </div>
      </Modal>

      {/* 导入 DSL */}
      <Modal
        open={importOpen}
        onClose={() => setImportOpen(false)}
        title={t("apps.importTitle")}
        width="max-w-2xl"
        footer={
          <div className="flex justify-end gap-2">
            <Button variant="secondary" size="sm" onClick={() => setImportOpen(false)}>
              {t("actions.cancel")}
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={() => importMutation.mutate()}
              loading={importMutation.isPending}
              disabled={!importYaml.trim()}
            >
              {t("apps.import")}
            </Button>
          </div>
        }
      >
        <div className="space-y-3">
          <div>
            <label className="block text-xs text-muted mb-1">{t("apps.fieldName")}</label>
            <Input
              value={importName}
              onChange={(e) => setImportName(e.target.value)}
              placeholder={t("apps.importNamePlaceholder")}
            />
          </div>
          <div>
            <label className="block text-xs text-muted mb-1">DSL (YAML)</label>
            <Textarea
              value={importYaml}
              onChange={(e) => setImportYaml(e.target.value)}
              rows={14}
              className="font-mono text-xs"
              placeholder="version: '0.5.0'&#10;kind: app&#10;app: ..."
            />
          </div>
        </div>
      </Modal>

      {/* DSL 编辑器 */}
      <Modal
        open={dslApp !== null}
        onClose={() => setDslApp(null)}
        title={dslApp ? t("apps.dslTitle", { name: dslApp.name }) : ""}
        width="max-w-4xl"
        footer={
          <div className="flex justify-end gap-2">
            <Button variant="secondary" size="sm" onClick={() => setDslApp(null)}>
              {t("actions.cancel")}
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => applyMutation.mutate(false)}
              loading={applyMutation.isPending && applyMutation.variables === false}
              disabled={!dslText.trim() || dslLoading}
            >
              <FileCode2 size={14} /> {t("apps.applyDraft")}
            </Button>
            {dslApp && WORKFLOW_MODES.has(dslApp.mode) && (
              <Button
                variant="primary"
                size="sm"
                onClick={() => applyMutation.mutate(true)}
                loading={applyMutation.isPending && applyMutation.variables === true}
                disabled={!dslText.trim() || dslLoading}
              >
                <Rocket size={14} /> {t("apps.applyPublish")}
              </Button>
            )}
          </div>
        }
      >
        {dslLoading ? (
          <div className="py-16 text-center text-sm text-muted">{t("apps.dslLoading")}</div>
        ) : (
          <CodeMirror
            value={dslText}
            height="55vh"
            extensions={[yamlLang()]}
            onChange={setDslText}
            basicSetup={{ lineNumbers: true, foldGutter: true }}
          />
        )}
      </Modal>

      <ConfirmDialog
        open={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => deleteTarget && deleteMutation.mutate(deleteTarget)}
        title={t("confirm.deleteTitle")}
        message={t("confirm.deleteMsg", { name: deleteTarget?.name ?? "" })}
        danger
        loading={deleteMutation.isPending}
      />
    </div>
  );
}
