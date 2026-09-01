import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Play } from "lucide-react";
import { sshApi } from "./api";
import { Card } from "@/components/common/Card";
import { toast } from "@/stores/toast-store";
import type { SshExecResult } from "./types";

export function ExecPanel() {
  const { t } = useTranslation("ssh");
  const [selected, setSelected] = useState("");
  const [command, setCommand] = useState("");
  const [timeout, setTimeout_] = useState(60);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<SshExecResult | null>(null);

  const { data } = useQuery({
    queryKey: ["sshConnections"],
    queryFn: () => sshApi.list().then((r) => r.data),
  });

  const connections = data?.connections ?? [];
  // 默认选中默认连接
  const effectiveSelected = selected || data?.default || connections[0]?.name || "";

  const handleExec = async () => {
    if (!command.trim() || !effectiveSelected) return;
    setRunning(true);
    setResult(null);
    try {
      const res = await sshApi.exec(effectiveSelected, { command: command.trim(), timeout });
      setResult(res.data);
    } catch (err) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(msg || t("messages.execFailed"));
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="space-y-4">
      <Card title={t("exec.title")} subtitle={t("exec.subtitle")}>
        <div className="space-y-3">
          <div className="flex flex-wrap gap-3">
            <div className="flex-1 min-w-[180px]">
              <label className="block text-xs font-medium text-muted mb-1">{t("exec.connection")}</label>
              <select
                value={effectiveSelected}
                onChange={(e) => setSelected(e.target.value)}
                className="w-full px-3 py-2 text-sm rounded-md border border-border bg-elevated text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
              >
                {connections.length === 0 && <option value="">{t("exec.noConnections")}</option>}
                {connections.map((c) => (
                  <option key={c.name} value={c.name}>
                    {c.name} ({c.username}@{c.host})
                  </option>
                ))}
              </select>
            </div>
            <div className="w-28">
              <label className="block text-xs font-medium text-muted mb-1">{t("exec.timeout")}</label>
              <input
                type="number"
                value={timeout}
                onChange={(e) => setTimeout_(parseInt(e.target.value, 10) || 60)}
                className="w-full px-3 py-2 text-sm rounded-md border border-border bg-elevated text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
              />
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium text-muted mb-1">{t("exec.command")}</label>
            <div className="flex gap-2">
              <input
                value={command}
                onChange={(e) => setCommand(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && !running) handleExec(); }}
                placeholder="uname -a && df -h"
                className="flex-1 px-3 py-2 text-sm font-mono rounded-md border border-border bg-elevated text-foreground placeholder:text-muted focus:outline-none focus:ring-1 focus:ring-accent"
              />
              <button
                onClick={handleExec}
                disabled={running || !command.trim() || !effectiveSelected}
                className="flex items-center gap-1.5 px-4 py-2 text-sm rounded-md bg-accent text-white hover:opacity-90 disabled:opacity-50 transition-all"
              >
                <Play size={15} /> {running ? t("exec.running") : t("exec.run")}
              </button>
            </div>
          </div>
        </div>
      </Card>

      {result && (
        <Card
          title={t("exec.result")}
          subtitle={`exit code: ${result.exit_code}${result.truncated ? ` · ${t("exec.truncated")}` : ""}`}
        >
          <div className="space-y-3">
            {result.stdout && (
              <div>
                <div className="text-xs font-medium text-muted mb-1">stdout</div>
                <pre className="p-3 rounded-md bg-elevated border border-border text-xs text-foreground overflow-x-auto whitespace-pre-wrap break-all max-h-64 overflow-y-auto">
                  {result.stdout}
                </pre>
              </div>
            )}
            {result.stderr && (
              <div>
                <div className="text-xs font-medium text-danger mb-1">stderr</div>
                <pre className="p-3 rounded-md bg-elevated border border-danger/30 text-xs text-danger overflow-x-auto whitespace-pre-wrap break-all max-h-48 overflow-y-auto">
                  {result.stderr}
                </pre>
              </div>
            )}
            {!result.stdout && !result.stderr && (
              <div className="text-sm text-muted">{t("exec.noOutput")}</div>
            )}
          </div>
        </Card>
      )}
    </div>
  );
}
