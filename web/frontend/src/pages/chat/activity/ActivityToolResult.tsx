import { useTranslation } from "react-i18next";
import { Markdown } from "../render/Markdown";
import { renderTaggedText, TaggedText, MemoryTags } from "./ActivityReferences";
import { TerminalBlock, toolText } from "@/components/activity/ToolPresentation";

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** 优先展示结构化回执中的正文与纠错提示，原始数据保留供完整核验。 */
export function ActivityToolResult({ result }: { result: string }) {
  const { t } = useTranslation("workbench");
  let payload: unknown;
  try { payload = JSON.parse(result); } catch { payload = null; }
  if (!record(payload)) return <div className="activity-result-text"><Markdown content={result} renderText={renderTaggedText} /></div>;
  const fields = ["error", "hint", "stdout", "stderr", "content", "output", "message"]
    .flatMap((key) => typeof payload[key] === "string" && payload[key] ? [{ key, value: payload[key] }] : []);
  const matches = Array.isArray(payload.results) ? payload.results.filter((item): item is Record<string, unknown> => record(item) && typeof item.id === "string" && typeof item.content === "string") : [];
  const facts = Object.entries(payload).filter(([key, value]) => !fields.some((field) => field.key === key) && toolText(value) !== "");
  if (!fields.length && !matches.length && !facts.length) return <pre>{JSON.stringify(payload, null, 2)}</pre>;
  return <>
    {facts.length > 0 && <dl className="tool-facts">{facts.map(([key, value]) => <div key={key}><dt>{t(`toolFields.${key}`, { defaultValue: key })}</dt><dd>{toolText(value)}</dd></div>)}</dl>}
    {!!matches.length && <div className="activity-recall-results">{matches.map((item, index) => <article key={index}>
      <header><span>{String(item.id)}</span>{typeof item.score === "number" && <span>{t("activity.recallScore", { score: item.score.toFixed(2) })}</span>}</header>
      <p><TaggedText content={String(item.content)} /></p>
      {Array.isArray(item.tags) && <MemoryTags tags={item.tags.filter((tag): tag is string => typeof tag === "string")} />}
    </article>)}</div>}
    <div className="activity-result-text">{fields.map(({ key, value }) => key === "stdout" || key === "stderr" ? <TerminalBlock key={key} label={key} text={value} warning={key === "stderr"} /> : <div key={key} className={key === "error" ? "text-warn" : undefined}>
      <h4>{t(`activity.resultFields.${key}`)}</h4><Markdown content={value} renderText={renderTaggedText} />
    </div>)}</div>
  </>;
}
