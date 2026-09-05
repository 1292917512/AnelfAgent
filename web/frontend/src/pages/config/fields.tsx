import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Switch } from "@/components/ui";
import { cn } from "@/lib/utils";

/**
 * 配置控件族：按 ConfigMetaItem.type 分发的统一交互约定——
 * Switch/Select 即改即存；Number/Text 失焦或回车提交；Range 拖动中仅本地预览、松手提交。
 */

interface CommonProps {
  disabled?: boolean;
}

export function SwitchField({
  value,
  disabled,
  onCommit,
}: CommonProps & { value: boolean; onCommit: (v: boolean) => void }) {
  return <Switch checked={value} disabled={disabled} onChange={onCommit} />;
}

export function SelectField({
  value,
  options,
  disabled,
  onCommit,
}: CommonProps & { value: string; options: string[]; onCommit: (v: string) => void }) {
  return (
    <select
      value={value}
      disabled={disabled}
      onChange={(e) => onCommit(e.target.value)}
      className="bg-bg border border-input rounded-md px-2.5 py-1.5 text-sm text-foreground outline-none focus:border-ring disabled:opacity-50"
    >
      {options.map((opt) => (
        <option key={opt} value={opt}>{opt}</option>
      ))}
    </select>
  );
}

const INPUT_CLS =
  "bg-bg border border-input rounded-md px-2.5 py-1.5 text-sm text-foreground outline-none focus:border-ring disabled:opacity-50";

export function NumberField({
  value,
  isFloat,
  unit,
  disabled,
  onCommit,
}: CommonProps & {
  value: number;
  isFloat?: boolean;
  unit?: string;
  onCommit: (v: number) => void;
}) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);

  const commit = () => {
    if (text.trim() === "") {
      setText(String(value));
      return;
    }
    const parsed = isFloat ? parseFloat(text) : parseInt(text, 10);
    if (Number.isNaN(parsed)) {
      setText(String(value));
      return;
    }
    if (parsed !== value) onCommit(parsed);
    else setText(String(parsed));
  };

  return (
    <span className="flex items-center gap-1.5">
      <input
        type="number"
        value={text}
        disabled={disabled}
        step={isFloat ? "any" : 1}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        }}
        className={cn(INPUT_CLS, "w-28")}
      />
      {unit && <span className="text-xs text-muted shrink-0">{unit}</span>}
    </span>
  );
}

export function TextField({
  value,
  disabled,
  onCommit,
}: CommonProps & { value: string; onCommit: (v: string) => void }) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);

  const commit = () => {
    if (text !== value) onCommit(text);
  };

  return (
    <input
      type="text"
      value={text}
      disabled={disabled}
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
      }}
      className={cn(INPUT_CLS, "w-48")}
    />
  );
}

/**
 * 敏感值控件（PASSWORD 类型）：服务端返回掩码值（abcd****wxyz），
 * 未改动时提交掩码由服务端识别并保留现值；输入新值即替换。
 */
export function PasswordField({
  value,
  disabled,
  onCommit,
}: CommonProps & { value: string; onCommit: (v: string) => void }) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);

  const commit = () => {
    if (text !== value) onCommit(text);
  };

  return (
    <input
      type="password"
      value={text}
      disabled={disabled}
      autoComplete="new-password"
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
      }}
      className={cn(INPUT_CLS, "w-48")}
    />
  );
}

/**
 * 滑条 + 数值复合控件（RANGE 类型）：拖动过程只更新本地预览，
 * pointerup/keyup 才提交——避免拖动中连续打后端。
 */
export function RangeField({
  value,
  min,
  max,
  step,
  unit,
  disabled,
  className,
  onCommit,
}: CommonProps & {
  value: number;
  min: number;
  max: number;
  step: number;
  unit?: string;
  /** 滑条宽度类名，默认 w-32 */
  className?: string;
  onCommit: (v: number) => void;
}) {
  const { t } = useTranslation("config");
  const [draft, setDraft] = useState(value);
  const [dragging, setDragging] = useState(false);
  useEffect(() => {
    if (!dragging) setDraft(value);
  }, [value, dragging]);

  const shown = dragging ? draft : value;
  const isInt = Number.isInteger(step);
  const display = isInt ? String(Math.round(shown)) : shown.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");

  return (
    <span className="flex items-center gap-2">
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={shown}
        disabled={disabled}
        aria-label={t("rangeAdjust")}
        onChange={(e) => {
          setDragging(true);
          setDraft(parseFloat(e.target.value));
        }}
        onPointerUp={() => {
          setDragging(false);
          if (draft !== value) onCommit(draft);
        }}
        onKeyUp={() => {
          setDragging(false);
          if (draft !== value) onCommit(draft);
        }}
        className={cn("accent-[var(--accent)] disabled:opacity-50", className ?? "w-32")}
      />
      <span className="w-14 text-right font-mono text-sm text-heading shrink-0">
        {display}
        {unit && <span className="text-xs text-muted font-sans ml-0.5">{unit}</span>}
      </span>
    </span>
  );
}
