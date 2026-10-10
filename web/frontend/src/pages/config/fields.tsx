import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Switch } from "@/components/ui";
import { ModelSelect } from "@/components/models/ModelSelect";
import { cn } from "@/lib/utils";

interface CommonProps {
  label?: string;
  disabled?: boolean;
}

const INPUT_CLS = "bg-card border border-input rounded-lg px-2.5 py-1.5 text-sm text-foreground outline-none focus:border-ring disabled:opacity-50";

export function SwitchField({ value, label, disabled, onCommit }: CommonProps & {
  value: boolean; onCommit: (value: boolean) => void;
}) {
  return <Switch label={label} checked={value} disabled={disabled} onChange={onCommit} />;
}

export function SelectField({ value, options, label, disabled, onCommit }: CommonProps & {
  value: string; options: string[]; onCommit: (value: string) => void;
}) {
  const { t } = useTranslation("config");
  return <select aria-label={label} value={value} disabled={disabled} onChange={(event) => onCommit(event.target.value)} className={cn(INPUT_CLS, "max-w-full")}>
    {options.map((option) => <option key={option} value={option}>{option === "" ? t("enumEmptyOption") : option}</option>)}
  </select>;
}

export function ModelField({ value, allowEmpty, label, disabled, onCommit }: CommonProps & {
  value: string; allowEmpty: boolean; onCommit: (value: string) => void;
}) {
  return <ModelSelect modelType="chat" value={value} label={label} allowEmpty={allowEmpty}
    allowPin={false} showDefaultWhenEmpty={false} compact className="w-44"
    onChange={onCommit} disabled={disabled} />;
}

export function NumberField({ value, isFloat, unit, min, max, label, disabled, onCommit }: CommonProps & {
  value: number; isFloat?: boolean; unit?: string; min?: number; max?: number;
  onCommit: (value: number) => void;
}) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  const commit = () => {
    if (!text.trim() || !Number.isFinite(Number(text))) { setText(String(value)); return; }
    let parsed = isFloat ? Number(text) : Math.trunc(Number(text));
    if (min !== undefined) parsed = Math.max(min, parsed);
    if (max !== undefined) parsed = Math.min(max, parsed);
    setText(String(parsed));
    if (parsed !== value) onCommit(parsed);
  };
  return <span className="flex items-center gap-1.5">
    <input type="number" aria-label={label} value={text} disabled={disabled} min={min} max={max}
      step={isFloat ? "any" : 1} onChange={(event) => setText(event.target.value)} onBlur={commit}
      onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }}
      className={cn(INPUT_CLS, "w-28")} />
    {unit && <span className="text-xs text-muted shrink-0">{unit}</span>}
  </span>;
}

type TextFieldProps = CommonProps & {
  value: string; multiline?: boolean; password?: boolean; onCommit: (value: string) => void;
};

export function TextField({ value, multiline, password, label, disabled, onCommit }: TextFieldProps) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  const commit = () => { if (text !== value) onCommit(text); };
  const props = { "aria-label": label, value: text, disabled, onBlur: commit, className: cn(INPUT_CLS, "w-48 max-w-full") };
  return multiline
    ? <textarea {...props} rows={3} onChange={(event) => setText(event.target.value)} />
    : <input {...props} type={password ? "password" : "text"} autoComplete={password ? "new-password" : "off"}
      onChange={(event) => setText(event.target.value)}
      onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }} />;
}

export function PasswordField(props: Omit<TextFieldProps, "password" | "multiline">) {
  return <TextField {...props} password />;
}

/** 滑条拖动期间保留预览值，松手或键盘操作结束时提交。 */
export function RangeField({ value, min, max, step, unit, label, disabled, className, onCommit }: CommonProps & {
  value: number; min: number; max: number; step: number; unit?: string; className?: string;
  onCommit: (value: number) => void;
}) {
  const { t } = useTranslation("config");
  const [draft, setDraft] = useState<number | null>(null);
  const shown = draft ?? value;
  const display = Number.isInteger(step) ? String(Math.round(shown)) : String(Number(shown.toFixed(4)));
  const commit = (next: number) => {
    setDraft(null);
    if (next !== value) onCommit(next);
  };
  return <span className="flex items-center gap-2">
    <input type="range" min={min} max={max} step={step} value={shown} disabled={disabled}
      aria-label={label ?? t("rangeAdjust")} onChange={(event) => setDraft(Number(event.target.value))}
      onPointerUp={(event) => commit(Number(event.currentTarget.value))}
      onKeyUp={(event) => commit(Number(event.currentTarget.value))}
      onBlur={(event) => { if (draft !== null) commit(Number(event.currentTarget.value)); }}
      onPointerCancel={() => setDraft(null)}
      className={cn("accent-[var(--accent)] disabled:opacity-50", className ?? "w-32")} />
    <span className="w-14 text-right font-mono text-sm text-heading shrink-0">{display}
      {unit && <span className="text-xs text-muted font-sans ml-0.5">{unit}</span>}
    </span>
  </span>;
}
