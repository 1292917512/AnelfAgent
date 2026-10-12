import { useTranslation } from "react-i18next";
import { Markdown } from "../render/Markdown";
import { renderTaggedText, TaggedText } from "./ActivityReferences";

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
  if (!fields.length && !matches.length) return <pre>{JSON.stringify(payload, null, 2)}</pre>;
  return <>
    {!!matches.length && <div className="activity-recall-results">{matches.map((item, index) => <article key={index}>
      <header><span>{String(item.id)}</span>{typeof item.score === "number" && <span>{t("activity.recallScore", { score: item.score.toFixed(2) })}</span>}</header>
      <p><TaggedText content={String(item.content)} /></p>
      {Array.isArray(item.tags) && <div className="activity-recall-tags">{item.tags.filter((tag): tag is string => typeof tag === "string").map((tag, i) => <span key={i}>{tag}</span>)}</div>}
    </article>)}</div>}
    <div className="activity-result-text">{fields.map(({ key, value }) => <div key={key} className={key === "error" || key === "stderr" ? "text-warn" : undefined}>
      <h4>{t(`activity.resultFields.${key}`)}</h4><Markdown content={value} renderText={renderTaggedText} />
    </div>)}</div>
    <details className="activity-result-raw"><summary>{t("activity.rawResult")}</summary><pre>{JSON.stringify(payload, null, 2)}</pre></details>
  </>;
}
