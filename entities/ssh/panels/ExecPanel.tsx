import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Play } from "lucide-react";
import { ConfigLink } from "@/components/common/ConfigLink";
import { sshApi } from "./api";
import { Card } from "@/components/common/Card";
import { NetworkConfigLink } from "@/components/common/NetworkSettings";
import { Select } from "@/components/ui";
import type { ProxyMode } from "@/lib/types";
import { toast } from "@/stores/toast-store";
import type { SshExecResult } from "./types";

export function ExecPanel() {
  const { t } = useTranslation("ssh");
  const [selected, setSelected] = useState("");
  const [command, setCommand] = useState("");
  const [timeout, setTimeout_] = useState(60);
  const [proxyMode, setProxyMode] = useState<ProxyMode | "">("");
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
      const res = await sshApi.exec(effectiveSelected, {
        command: command.trim(), timeout, ...(proxyMode ? { proxy_mode: proxyMode } : {}),
      });
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
              <Select
                value={effectiveSelected}
                onChange={(e) => setSelected(e.target.value)}
                className="w-full"
              >
                {connections.length === 0 && <option value="">{t("exec.noConnections")}</option>}
                {connections.map((c) => (
                  <option key={c.name} value={c.name}>
                    {c.name} ({c.username}@{c.host})
                  </option>
                ))}
              </Select>
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
          <div className="space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <label htmlFor="ssh-exec-proxy" className="text-xs font-medium text-muted">{t("exec.proxyMode")}</label>
              <div className="flex flex-wrap items-center gap-3">
                <ConfigLink config={{ key: "ssh_proxy_mode" }} target="_blank" rel="noreferrer">{t("exec.configureDefault")}</ConfigLink>
                <NetworkConfigLink />
              </div>
            </div>
            <Select id="ssh-exec-proxy" value={proxyMode} className="w-full" disabled={running} onChange={(event) => {
              const mode = event.target.value;
              if (mode === "" || mode === "direct" || mode === "system" || mode === "proxy") setProxyMode(mode);
            }}>
              <option value="">{t("exec.followDefault")}</option>
              {(["direct", "system", "proxy"] as const).map((mode) => (
                <option key={mode} value={mode}>{t(`config:network.${mode}`)}</option>
              ))}
            </Select>
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
            {(result.login_user || result.work_dir) && <dl className="flex flex-wrap gap-x-6 gap-y-2 text-xs">
              {result.login_user && <div><dt className="text-muted">{t("exec.loginIdentity")}</dt><dd className="break-all">{result.login_user}@{result.host}</dd></div>}
              {result.work_dir && <div><dt className="text-muted">{t("exec.workDir")}</dt><dd className="break-all">{result.work_dir}</dd></div>}
            </dl>}
            {result.error && <div role="status" className="space-y-1 rounded-md border border-warn/30 bg-warn-subtle p-3 text-sm break-words">
              <p className="text-warn">{result.error}</p>
              {result.hint && <p className="text-xs text-muted">{result.hint}</p>}
            </div>}
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
