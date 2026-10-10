import { cn } from "@/lib/utils";

/** 统一开关 */
export function Switch({
  checked,
  label,
  onChange,
  disabled = false,
}: {
  checked: boolean;
  label?: string;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="app-switch grid h-9 w-11 shrink-0 place-items-center rounded-lg disabled:opacity-50 disabled:cursor-not-allowed"
    >
      <span aria-hidden className={cn("relative inline-flex h-5 w-9 items-center rounded-full transition-colors", checked ? "bg-accent" : "bg-border-strong")}>
      <span
        className={cn(
          "inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform",
          checked ? "translate-x-4.5" : "translate-x-1",
        )}
      />
      </span>
    </button>
  );
}
