import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Play } from "lucide-react";
import { difyApi } from "./api";
import { Card } from "@/components/common/Card";
import { Button, Select, Textarea, toast } from "@/components/ui";
import { apiErrorMessage } from "@/lib/api";

const WORKFLOW_MODES = new Set(["workflow"]);

export function RunTab() {
  const { t } = useTranslation("dify");
  const [appId, setAppId] = useState("");
  const [query, setQuery] = useState("");
  const [inputsJson, setInputsJson] = useState("{}");
  const [conversationId, setConversationId] = useState("");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState("");

  const { data } = useQuery({
    queryKey: ["dify-apps"],
    queryFn: () => difyApi.listApps().then((r) => r.data),
  });
  const apps = (data?.apps ?? []).filter((a) => a.has_api_key || true);
  const selected = apps.find((a) => a.id === appId);
  const isWorkflow = selected ? WORKFLOW_MODES.has(selected.mode) : false;

  const parseInputs = (): Record<string, unknown> | null => {
    try {
      const parsed = JSON.parse(inputsJson || "{}");
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        throw new Error("not object");
      }
      return parsed as Record<string, unknown>;
    } catch {
      toast.error(t("run.inputsInvalid"));
      return null;
    }
  };

  const onRun = async () => {
    if (!appId) {
      toast.error(t("run.selectApp"));
      return;
    }
    const inputs = parseInputs();
    if (inputs === null) return;
    if (!isWorkflow && !query.trim()) {
      toast.error(t("run.queryRequired"));
      return;
    }
    setRunning(true);
    setResult("");
    try {
      if (isWorkflow) {
        const resp = await difyApi.runWorkflow(appId, inputs);
        const d = resp.data;
        setResult(
          JSON.stringify(
            { status: d.status, outputs: d.outputs, error: d.error, elapsed_time: d.elapsed_time, total_tokens: d.total_tokens },
            null,
            2,
          ),
        );
      } else {
        const resp = await difyApi.chat(appId, query.trim(), inputs, conversationId);
        const d = resp.data;
        if (d.conversation_id) setConversationId(d.conversation_id);
        setResult(d.answer || JSON.stringify(d, null, 2));
      }
    } catch (err) {
      toast.error(apiErrorMessage(err, t("messages.actionFailed")));
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="space-y-4 max-w-3xl">
      <Card title={t("run.title")} subtitle={t("run.desc")}>
        <div className="space-y-3">
          <div>
            <label className="block text-xs text-muted mb-1">{t("run.app")}</label>
            <Select
              value={appId}
              onChange={(e) => {
                setAppId(e.target.value);
                setConversationId("");
              }}
              className="w-full"
            >
              <option value="">{t("run.selectPlaceholder")}</option>
              {apps.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}（{a.mode}）
                </option>
              ))}
            </Select>
          </div>

          {selected && !isWorkflow && (
            <div>
              <label className="block text-xs text-muted mb-1">{t("run.query")}</label>
              <Textarea
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                rows={3}
                placeholder={t("run.queryPlaceholder")}
              />
              {conversationId && (
                <p className="mt-1 text-[11px] text-muted">
                  {t("run.conversation")}: {conversationId}
                </p>
              )}
            </div>
          )}

          <div>
            <label className="block text-xs text-muted mb-1">{t("run.inputs")}</label>
            <Textarea
              value={inputsJson}
              onChange={(e) => setInputsJson(e.target.value)}
              rows={3}
              className="font-mono text-xs"
              placeholder='{"key": "value"}'
            />
          </div>

          <Button variant="primary" size="sm" onClick={onRun} loading={running} disabled={!appId}>
            <Play size={14} /> {t("run.execute")}
          </Button>
        </div>
      </Card>

      {(result || running) && (
        <Card title={t("run.result")}>
          <pre className="text-xs font-mono text-foreground bg-elevated border border-border rounded-md p-3 overflow-auto max-h-96 whitespace-pre-wrap">
            {running ? t("run.running") : result}
          </pre>
        </Card>
      )}
    </div>
  );
}
