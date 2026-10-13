import type { ReactNode } from "react";
import { Terminal, Copy, Check } from "lucide-react";
import { useTranslation } from "react-i18next";
import { activityRecord } from "@/lib/activity-data";
import type { ToolRendererProps } from "@/lib/ui-contributions";
import { useCopyFeedback } from "@/hooks/useCopyFeedback";

export interface ToolField { key: string; label?: string; code?: boolean }

export function toolText(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value) && value.every((item) => typeof item === "string")) return value.join(", ");
  return "";
}

/** 模块选择可公开的摘要字段；完整参数由宿主保留在详情中。 */
export function ToolFacts({ entry, fields }: Pick<ToolRendererProps, "entry"> & { fields: readonly ToolField[] }) {
  const { t } = useTranslation("workbench");
  const args = activityRecord(entry.arguments);
  const values = fields.flatMap((field) => { const value = toolText(args[field.key]); return value ? [{ ...field, value }] : []; });
  if (!values.length) return null;
  return <dl className="tool-facts">{values.map((field) => <div key={field.key}><dt>{field.label ?? t(`toolFields.${field.key}`, { defaultValue: field.key })}</dt><dd className={field.code ? "font-mono" : undefined} title={field.value}>{field.value}</dd></div>)}</dl>;
}

export function ToolPresentation({ fields, ...props }: ToolRendererProps & { fields: readonly ToolField[] }) {
  return props.view === "summary" ? <ToolFacts entry={props.entry} fields={fields} /> : props.fallback;
}

export function TerminalBlock({ label, text, warning = false }: { label: ReactNode; text: string; warning?: boolean }) {
  const { t } = useTranslation("common");
  const [copied, markCopied] = useCopyFeedback();
  return <section className="tool-terminal" data-warning={warning || undefined}>
    <header><span>{label}</span>{typeof navigator.clipboard?.writeText === "function" && <button type="button" aria-label={t("copy")} onClick={() => { void navigator.clipboard.writeText(text).then(markCopied).catch(() => {}); }}>{copied ? <Check size={12} /> : <Copy size={12} />}</button>}</header>
    <pre><code>{text}</code></pre>
  </section>;
}

/** 命令与终端回执按原文显示，不把输出解释成 Markdown 或 HTML。 */
export function CommandExecution({ command, target, ...props }: ToolRendererProps & { command: string; target: string }) {
  const { t } = useTranslation("workbench");
  const result = activityRecord(props.entry.result);
  const exitCode = result.exit_code ?? result.returncode;
  const cwd = toolText(result.work_dir ?? result.shell_cwd ?? result.cwd);
  const background = result.status === "running" || result.status === "started" || Boolean(result.task_id);
  if (props.view === "summary") return <div className="tool-command-summary"><div><Terminal size={13} /><span>{target}</span>{cwd && <span title={cwd}>{cwd}</span>}{typeof exitCode === "number" && <b data-failed={exitCode !== 0}>{t("toolView.exitCode", { code: exitCode })}</b>}{background && <b>{t("toolView.background")}</b>}</div><pre title={command}>{command}</pre></div>;
  const streams = ["stdout", "stderr", "output", "output_so_far"].flatMap((key) => typeof result[key] === "string" && result[key] ? [{ key, text: result[key] }] : []);
  return <div className="tool-command-detail">
    {streams.map(({ key, text }) => <TerminalBlock key={key} label={key} text={text} warning={key === "stderr"} />)}
    {props.entry.result && !streams.length && !Object.keys(result).length && <TerminalBlock label={t("activity.result")} text={props.entry.result} />}
    {streams.length === 0 && props.entry.status === "done" && !background && Object.keys(result).length > 0 && <p className="activity-note">{t("toolView.noOutput")}</p>}
    {Object.keys(result).length > 0 && <dl className="tool-facts">{["error", "hint", "cause", "retryable", "outcome", "message", "connection", "cwd", "shell_cwd", "work_dir", "task_id", "output_file", "persisted", "notes", "calls", "completed_calls", "pending_tool"].map((key) => { const value = toolText(result[key]); return value ? <div key={key}><dt>{t(`toolFields.${key}`, { defaultValue: key })}</dt><dd>{value}</dd></div> : null; })}</dl>}
  </div>;
}
