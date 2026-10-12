import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Play, Trash2 } from "lucide-react";
import { hooksApi } from "@/lib/api";
import type { HookEntry } from "@/lib/types";
import {
  Badge,
  Button,
  Input,
  Select,
  Switch,
  Textarea,
} from "@/components/ui";
import { toast } from "@/stores/toast-store";

export function ScriptHookEditor({
  row,
  onChange,
  onRemove,
}: {
  row: HookEntry;
  onChange: (value: Partial<HookEntry>) => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation("settings");
  return (
    <div className="rounded-xl border border-border bg-elevated/30 p-4">
      <div className="mb-4 flex items-center gap-3">
        <Switch
          checked={row.enabled !== false}
          label={t("hooks.rowToggle")}
          onChange={(enabled) => onChange({ enabled })}
        />
        <span className="text-xs text-muted">
          {t(
            row.enabled === false ? "hooks.rowDisabled" : "hooks.enabledLabel",
          )}
        </span>
        <Button
          className="ml-auto"
          size="sm"
          variant="ghost"
          aria-label={t("common:delete")}
          onClick={onRemove}
        >
          <Trash2 size={15} />
        </Button>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-[1fr_1fr_7rem]">
        <label className="col-span-2 space-y-1.5 text-xs text-muted sm:col-span-1">
          <span>{t("hooks.matcher")}</span>
          <Input
            value={row.matcher}
            onChange={(e) => onChange({ matcher: e.target.value })}
            placeholder="*"
          />
        </label>
        <label className="space-y-1.5 text-xs text-muted">
          <span>{t("hookFlow.mode")}</span>
          <Select
            className="w-full"
            value={row.mode ?? "sync"}
            onChange={(e) =>
              onChange({ mode: e.target.value === "async" ? "async" : "sync" })
            }
          >
            <option value="sync">{t("hookFlow.sync")}</option>
            <option value="async">{t("hookFlow.async")}</option>
          </Select>
        </label>
        <label className="space-y-1.5 text-xs text-muted">
          <span>{t("hooks.timeout")}</span>
          <Input
            type="number"
            min={1}
            max={60}
            value={
              Number.isFinite(row.timeout ?? 10) ? (row.timeout ?? 10) : ""
            }
            onChange={(e) => onChange({ timeout: e.target.valueAsNumber })}
          />
        </label>
      </div>
      <label className="mt-4 block space-y-1.5 text-xs text-muted">
        <span>{t("hooks.command")}</span>
        <Textarea
          className="font-mono text-xs"
          rows={3}
          value={row.command}
          onChange={(e) => onChange({ command: e.target.value })}
          placeholder={t("hooks.commandPlaceholder")}
        />
      </label>
      <p className="mt-2 text-xs leading-5 text-muted">
        {t(row.mode === "async" ? "hookFlow.asyncHint" : "hookFlow.syncHint")}
      </p>
    </div>
  );
}

export function ScriptHookTest({
  event,
  dirty,
}: {
  event: string;
  dirty: boolean;
}) {
  const { t } = useTranslation("settings");
  const client = useQueryClient();
  const [toolName, setToolName] = useState("*");
  const test = useMutation({
    mutationFn: () => hooksApi.test(event, toolName),
    onSuccess: () => client.invalidateQueries({ queryKey: ["hooks-config"] }),
    onError: () => toast.error(t("hooks.testFailed")),
  });
  return (
    <details className="mt-4 border-t border-border pt-3">
      <summary className="cursor-pointer py-1 text-xs text-muted">
        {t("hooks.testLabel")}
      </summary>
      <p className="my-3 text-xs leading-5 text-muted">
        {t(dirty ? "hookFlow.saveFirst" : "hookFlow.testHint")}
      </p>
      <div className="flex flex-wrap gap-2">
        {event !== "reply_end" && (
          <Input
            className="min-w-0 flex-1"
            aria-label={t("hooks.testToolLabel")}
            value={toolName}
            onChange={(e) => setToolName(e.target.value)}
          />
        )}
        <Button
          variant="secondary"
          disabled={dirty}
          loading={test.isPending}
          onClick={() => test.mutate()}
        >
          <Play size={14} />
          {t("hooks.test")}
        </Button>
      </div>
      {test.data && (
        <div className="mt-3 space-y-2">
          {test.data.data.results.length ? (
            test.data.data.results.map((result, index) => (
              <div
                key={index}
                className="rounded-lg bg-elevated/50 p-3 text-xs"
              >
                <div className="flex items-center gap-2">
                  <Badge variant={result.ok ? "ok" : "warn"}>
                    {result.blocked
                      ? t("hooks.blockedBadge")
                      : result.ok
                        ? "exit 0"
                        : `exit ${result.returncode ?? "?"}`}
                  </Badge>
                  <span className="text-muted">{result.duration_ms}ms</span>
                </div>
                <p className="mt-2 break-all font-mono">{result.command}</p>
                {result.detail && (
                  <p className="mt-2 whitespace-pre-wrap break-words text-muted">
                    {result.detail}
                  </p>
                )}
              </div>
            ))
          ) : (
            <p className="text-xs text-muted">{t("hooks.testNoMatch")}</p>
          )}
        </div>
      )}
    </details>
  );
}
