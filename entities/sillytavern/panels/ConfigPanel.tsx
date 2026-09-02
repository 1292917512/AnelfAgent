import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiErrorMessage, sillytavernApi } from "./api";
import type { StConfig } from "./types";
import { Button, Input, LoadingBlock, Switch, toast } from "@/components/ui";
import { Card } from "@/components/common/Card";

interface FormState {
  st_dir: string;
  port: string;
  listen: boolean;
  disable_csrf: boolean;
  extra_args: string;
  auto_start: boolean;
  context_inject: boolean;
  context_max_tokens: string;
  startup_timeout: string;
}

function toForm(c: StConfig): FormState {
  return {
    st_dir: c.st_dir ?? "",
    port: String(c.port ?? 8000),
    listen: !!c.listen,
    disable_csrf: !!c.disable_csrf,
    extra_args: c.extra_args ?? "",
    auto_start: !!c.auto_start,
    context_inject: !!c.context_inject,
    context_max_tokens: String(c.context_max_tokens ?? 2000),
    startup_timeout: String(c.startup_timeout ?? 120),
  };
}

function ToggleField({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex items-center justify-between gap-3 rounded-md border border-border bg-elevated px-3 py-2.5">
      <span className="min-w-0">
        <span className="block text-sm text-foreground">{label}</span>
        {hint && <span className="block text-xs text-muted mt-0.5">{hint}</span>}
      </span>
      <Switch checked={checked} onChange={onChange} />
    </label>
  );
}

/** 实体配置面板：端口/监听/CSRF/自启/上下文注入等 */
export function ConfigPanel() {
  const { t } = useTranslation(["sillytavern", "common"]);
  const queryClient = useQueryClient();

  const { data: config, isLoading } = useQuery({
    queryKey: ["st", "config"],
    queryFn: () => sillytavernApi.getConfig().then((r) => r.data),
  });

  const [form, setForm] = useState<FormState | null>(null);

  useEffect(() => {
    if (config) setForm(toForm(config));
  }, [config]);

  const saveMut = useMutation({
    mutationFn: () =>
      sillytavernApi.saveConfig({
        st_dir: form!.st_dir,
        port: Number(form!.port) || 8000,
        listen: form!.listen,
        disable_csrf: form!.disable_csrf,
        extra_args: form!.extra_args,
        auto_start: form!.auto_start,
        context_inject: form!.context_inject,
        context_max_tokens: Number(form!.context_max_tokens) || 2000,
        startup_timeout: Number(form!.startup_timeout) || 120,
      }),
    onSuccess: () => {
      toast.success(t("sillytavern:common.saved"));
      queryClient.invalidateQueries({ queryKey: ["st"] });
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("sillytavern:common.saveFailed"))),
  });

  if (isLoading || !form) return <LoadingBlock label={t("common:loading")} />;

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((f) => (f ? { ...f, [key]: value } : f));

  return (
    <Card
      title={t("sillytavern:config.title")}
      subtitle={t("sillytavern:config.subtitle")}
      actions={
        <Button variant="primary" size="sm" loading={saveMut.isPending} onClick={() => saveMut.mutate()}>
          {saveMut.isPending ? t("sillytavern:common.saving") : t("sillytavern:common.save")}
        </Button>
      }
    >
      <div className="space-y-4 max-w-2xl">
        <label className="block">
          <span className="text-xs font-medium text-muted">{t("sillytavern:config.stDir")}</span>
          <Input
            value={form.st_dir}
            onChange={(e) => set("st_dir", e.target.value)}
            className="mt-1 font-mono"
          />
        </label>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block">
            <span className="text-xs font-medium text-muted">{t("sillytavern:config.port")}</span>
            <Input
              type="number"
              value={form.port}
              onChange={(e) => set("port", e.target.value)}
              className="mt-1"
            />
          </label>
          <label className="block">
            <span className="text-xs font-medium text-muted">
              {t("sillytavern:config.startupTimeout")}
            </span>
            <Input
              type="number"
              value={form.startup_timeout}
              onChange={(e) => set("startup_timeout", e.target.value)}
              className="mt-1"
            />
          </label>
          <label className="block">
            <span className="text-xs font-medium text-muted">
              {t("sillytavern:config.contextMaxTokens")}
            </span>
            <Input
              type="number"
              value={form.context_max_tokens}
              onChange={(e) => set("context_max_tokens", e.target.value)}
              className="mt-1"
            />
          </label>
          <label className="block">
            <span className="text-xs font-medium text-muted">
              {t("sillytavern:config.extraArgs")}
            </span>
            <Input
              value={form.extra_args}
              onChange={(e) => set("extra_args", e.target.value)}
              placeholder={t("sillytavern:config.extraArgsPlaceholder")}
              className="mt-1 font-mono"
            />
          </label>
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          <ToggleField
            label={t("sillytavern:config.listen")}
            hint={t("sillytavern:config.listenHint")}
            checked={form.listen}
            onChange={(v) => set("listen", v)}
          />
          <ToggleField
            label={t("sillytavern:config.disableCsrf")}
            hint={t("sillytavern:config.disableCsrfHint")}
            checked={form.disable_csrf}
            onChange={(v) => set("disable_csrf", v)}
          />
          <ToggleField
            label={t("sillytavern:config.autoStart")}
            checked={form.auto_start}
            onChange={(v) => set("auto_start", v)}
          />
          <ToggleField
            label={t("sillytavern:config.contextInject")}
            checked={form.context_inject}
            onChange={(v) => set("context_inject", v)}
          />
        </div>
      </div>
    </Card>
  );
}
