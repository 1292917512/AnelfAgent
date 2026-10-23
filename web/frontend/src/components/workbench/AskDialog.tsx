import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation } from "@tanstack/react-query";
import { Check, Clock3, MessageCircleQuestion } from "lucide-react";
import { Button, Modal, Textarea } from "@/components/ui";
import { QueryError } from "@/components/common/AsyncState";
import { uiApi } from "@/lib/api";
import type { UiAsk } from "@/lib/types";
import { cn } from "@/lib/utils";

/** 保留问题正文到退场结束；只有显式提交才发送所选答案。 */
export function AskDialog({ ask, active, count, onAfterClose }: {
  ask: UiAsk; active: boolean; count: number; onAfterClose: () => void;
}) {
  const { t } = useTranslation("workbench");
  const formId = useId();
  const [open, setOpen] = useState(true);
  const [freeText, setFreeText] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!ask.expires_at) return;
    const tick = () => setNow(Date.now());
    const timer = setInterval(tick, 1000);
    document.addEventListener("visibilitychange", tick);
    return () => { clearInterval(timer); document.removeEventListener("visibilitychange", tick); };
  }, [ask.expires_at]);
  const submit = useMutation({
    mutationFn: (value: string) => uiApi.answer(ask.ask_id, value).then((response) => response.data),
    onSuccess: (data) => { if (data.status === "ok") setOpen(false); },
  });
  const remaining = ask.expires_at ? Math.max(0, Math.ceil(ask.expires_at - now / 1000)) : null;
  const expired = submit.data?.status === "expired" || remaining === 0;
  const answer = freeText.trim() || selected;
  const close = () => {
    if (submit.isPending) return;
    if (expired) setOpen(false);
    else submit.mutate("__skipped__");
  };
  return <Modal open={open && active} onClose={close} onAfterClose={onAfterClose}
    title={<span className="flex items-center gap-2.5"><MessageCircleQuestion size={20} className="shrink-0 text-accent" />{t("ask.title")}</span>}
    width="max-w-xl" dismissible={!submit.isPending}
    footer={<div className="flex w-full items-center justify-end gap-3">
      <Button className="min-w-24 flex-1 sm:flex-none" variant="secondary" disabled={submit.isPending} onClick={close}>
        {t(expired ? "common:close" : "ask.skip")}
      </Button>
      {!expired && <Button className="min-w-24 flex-1 sm:flex-none" variant="primary" form={formId} type="submit"
        disabled={!answer || !active} loading={submit.isPending}>{t("ask.submit")}</Button>}
    </div>}>
    <form id={formId} className="space-y-5" onSubmit={(event) => {
      event.preventDefault();
      if (answer && !expired && active && !submit.isPending) submit.mutate(answer);
    }}>
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
        <span>{t(count > 1 ? "ask.queued" : "ask.waiting", { count })}</span>
        {remaining !== null && !expired && <span className="inline-flex items-center gap-1.5 tabular-nums">
          <Clock3 size={13} aria-hidden="true" />{t("ask.remaining", { seconds: remaining })}
        </span>}
      </div>
      <p className="whitespace-pre-wrap break-words text-base font-medium leading-relaxed text-heading">{ask.question}</p>
      {submit.error && <QueryError compact error={submit.error} title={t("common:requestFailed")} />}
      {expired ? <p role="status" className="rounded-lg border border-border bg-elevated p-3 text-sm text-warn">{t("ask.expired")}</p> : <>
        {ask.options.length > 0 && <fieldset className="space-y-2" disabled={submit.isPending}>
          <legend className="mb-2 text-xs text-muted">{t("ask.choose")}</legend>
          {ask.options.map((option, index) => <label key={`${option}-${index}`} className={cn(
            "flex cursor-pointer items-start gap-3 rounded-xl border p-3 transition-colors focus-within:ring-2 focus-within:ring-accent",
            selected === option ? "border-accent bg-accent/10" : "border-border bg-elevated/40 hover:bg-hover",
            submit.isPending && "pointer-events-none opacity-60",
          )}>
            <input type="radio" className="sr-only" name={`${formId}-choice`} value={option} checked={selected === option}
              onChange={() => { setSelected(option); setFreeText(""); }} />
            <span aria-hidden="true" className={cn("mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border",
              selected === option ? "border-accent bg-accent text-primary-foreground" : "border-border-strong")}>
              {selected === option && <Check size={13} />}
            </span>
            <span className="min-w-0 whitespace-pre-wrap break-words text-sm leading-relaxed">{option}</span>
          </label>)}
        </fieldset>}
        <div className="space-y-2">
          <label htmlFor={`${formId}-answer`} className="text-xs font-medium text-muted">{t(ask.options.length ? "ask.custom" : "ask.answer")}</label>
          <Textarea id={`${formId}-answer`} rows={3} aria-label={t("ask.inputPlaceholder")} value={freeText}
            onChange={(event) => { setFreeText(event.target.value); setSelected(null); }}
            placeholder={t("ask.inputPlaceholder")} disabled={submit.isPending} className="min-h-24 max-h-52" />
        </div>
        <p className="text-xs leading-relaxed text-muted">{t("ask.hint")}</p>
      </>}
    </form>
  </Modal>;
}
