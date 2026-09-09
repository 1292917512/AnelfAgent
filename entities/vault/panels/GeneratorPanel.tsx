import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, Copy, RefreshCw } from "lucide-react";
import { vaultApi, vaultErrorMessage } from "./api";
import { Card } from "@/components/common/Card";
import { toast } from "@/stores/toast-store";
import type { VaultGenerateResult } from "./types";

const labelCls = "block text-xs font-medium text-muted mb-1";

/** 强密码生成器：字符集 / 长度 / 排除歧义 / 可读模式 + 强度评估。 */
export function GeneratorPanel() {
  const { t } = useTranslation("vault");
  const [length, setLength] = useState(20);
  const [symbols, setSymbols] = useState(true);
  const [excludeAmbiguous, setExcludeAmbiguous] = useState(false);
  const [memorable, setMemorable] = useState(false);
  const [result, setResult] = useState<VaultGenerateResult | null>(null);
  const [pending, setPending] = useState(false);

  const generate = async () => {
    setPending(true);
    try {
      const { data } = await vaultApi.generate({
        length, symbols, exclude_ambiguous: excludeAmbiguous, memorable,
      });
      setResult(data);
    } catch (err) {
      toast.error(vaultErrorMessage(err, t("messages.generateFailed")));
    } finally {
      setPending(false);
    }
  };

  const levelColor = {
    weak: "text-danger",
    fair: "text-warn",
    strong: "text-ok",
    excellent: "text-accent",
  } as const;

  return (
    <Card
      title={t("generator.title")}
      subtitle={t("generator.subtitle")}
      actions={
        <button
          onClick={generate}
          disabled={pending}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md bg-accent text-white hover:opacity-90 disabled:opacity-50 transition-all"
        >
          <RefreshCw size={14} /> {t("actions.generate")}
        </button>
      }
    >
      <div className="space-y-4">
        <div>
          <label className={labelCls}>
            {t("generator.length")}: {length}
          </label>
          <input
            type="range" min={8} max={64} step={1} value={length}
            onChange={(e) => setLength(Number(e.target.value))}
            className="w-full accent-accent"
            disabled={memorable}
          />
        </div>
        <div className="flex flex-wrap gap-4">
          {(
            [
              [symbols, setSymbols, "generator.symbols"],
              [excludeAmbiguous, setExcludeAmbiguous, "generator.excludeAmbiguous"],
              [memorable, setMemorable, "generator.memorable"],
            ] as const
          ).map(([value, setter, key]) => (
            <label key={key} className="flex items-center gap-2 text-sm text-heading cursor-pointer">
              <input
                type="checkbox" checked={value}
                onChange={(e) => setter(e.target.checked)}
                className="accent-accent"
              />
              {t(key)}
            </label>
          ))}
        </div>

        {result && (
          <div className="p-4 rounded-lg border border-border bg-elevated space-y-2">
            <div className="flex items-center gap-3">
              <code className="flex-1 text-base font-mono text-heading break-all select-all">
                {result.password}
              </code>
              <button
                onClick={async () => {
                  await navigator.clipboard.writeText(result.password);
                  toast.success(t("messages.copied"));
                }}
                title={t("actions.copy")}
                className="p-1.5 rounded-md text-muted hover:text-accent hover:bg-hover transition-all shrink-0"
              >
                <Copy size={15} />
              </button>
            </div>
            <div className="flex items-center gap-2 text-xs">
              <Check size={12} className={levelColor[result.strength.level]} />
              <span className={levelColor[result.strength.level]}>
                {t(`generator.level.${result.strength.level}`)}
              </span>
              <span className="text-muted">
                {t("generator.entropy", { bits: result.strength.entropy })}
              </span>
              {result.strength.issues.map((issue) => (
                <span key={issue} className="text-warn">· {issue}</span>
              ))}
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}
