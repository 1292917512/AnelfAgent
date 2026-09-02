import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PlugZap } from "lucide-react";
import { apiErrorMessage, sillytavernApi } from "./api";
import type { StModelUpdatePayload } from "./types";
import { Button, Input, LoadingBlock, Select, toast } from "@/components/ui";
import { Card } from "@/components/common/Card";

const MAIN_APIS = [
  "openai",
  "textgenerationwebui",
  "novelai",
  "koboldhorde",
  "openrouter",
] as const;

/** 模型配置面板：读写酒馆 settings.json 的模型参数 */
export function ModelPanel() {
  const { t } = useTranslation(["sillytavern", "common"]);
  const queryClient = useQueryClient();

  const { data: status } = useQuery({
    queryKey: ["st", "status"],
    queryFn: () => sillytavernApi.status().then((r) => r.data),
    refetchInterval: 15_000,
  });

  const { data: settings, isLoading } = useQuery({
    queryKey: ["st", "settings"],
    queryFn: () => sillytavernApi.settings().then((r) => r.data),
    enabled: !!status?.running,
    retry: false,
  });

  // AnelfAgent 已配置的可对话模型（供一键直连）
  const { data: myModels } = useQuery({
    queryKey: ["st", "my-models"],
    queryFn: () => sillytavernApi.myModels().then((r) => r.data),
    enabled: !!status?.running,
    retry: false,
  });
  const [selectedMyModel, setSelectedMyModel] = useState("");

  const useMyMut = useMutation({
    mutationFn: (modelId: string) => sillytavernApi.useMyModel(modelId),
    onSuccess: (r) => {
      toast.success(
        t("sillytavern:model.linked", { model: r.data.model }),
      );
      queryClient.invalidateQueries({ queryKey: ["st", "settings"] });
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("sillytavern:common.requestFailed"))),
  });

  const [form, setForm] = useState({
    main_api: "openai",
    model: "",
    temperature: "1.0",
    max_context: "4096",
    max_tokens: "400",
  });

  useEffect(() => {
    if (!settings) return;
    setForm({
      main_api: typeof settings.main_api === "string" ? settings.main_api : "openai",
      model: typeof settings.model === "string" ? settings.model : "",
      temperature: String(settings.temperature ?? 1.0),
      max_context: String(settings.max_context ?? 4096),
      max_tokens: String(settings.max_tokens ?? 400),
    });
  }, [settings]);

  const saveMut = useMutation({
    mutationFn: (data: StModelUpdatePayload) => sillytavernApi.saveModel(data),
    onSuccess: (r) => {
      if (r.data.changed?.length) {
        toast.success(
          t("sillytavern:model.changed", { fields: r.data.changed.join(", ") }),
        );
      } else {
        toast.info(t("sillytavern:model.noChange"));
      }
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("sillytavern:common.requestFailed"))),
  });

  if (status && !status.running) {
    return (
      <Card title={t("sillytavern:model.title")} subtitle={t("sillytavern:common.notRunning")}>
        <p className="text-sm text-muted">{t("sillytavern:common.notRunning")}</p>
      </Card>
    );
  }

  if (isLoading) return <LoadingBlock label={t("common:loading")} />;

  return (
      <div className="space-y-4">
      {/* 直连 AnelfAgent 模型 */}
      <Card
        title={t("sillytavern:model.directLink")}
        subtitle={t("sillytavern:model.directLinkHint")}
      >
        <div className="flex flex-wrap items-end gap-3">
          <label className="block min-w-64 flex-1">
            <span className="text-xs font-medium text-muted">
              {t("sillytavern:model.pickModel")}
            </span>
            <Select
              value={selectedMyModel}
              onChange={(e) => setSelectedMyModel(e.target.value)}
              className="mt-1 w-full"
            >
              <option value="">{t("sillytavern:model.pickPlaceholder")}</option>
              {(myModels?.models ?? []).map((m) => (
                <option key={m.model_id} value={m.model_id}>
                  {m.provider_name} · {m.model}
                </option>
              ))}
            </Select>
          </label>
          <Button
            variant="primary"
            size="md"
            loading={useMyMut.isPending}
            disabled={!selectedMyModel}
            onClick={() => useMyMut.mutate(selectedMyModel)}
          >
            <PlugZap size={14} />
            {t("sillytavern:model.apply")}
          </Button>
        </div>
      </Card>

      {/* 酒馆模型参数 */}
      <Card
      title={t("sillytavern:model.title")}
      subtitle={t("sillytavern:model.subtitle")}
      actions={
        <Button
          variant="primary"
          size="sm"
          loading={saveMut.isPending}
          onClick={() =>
            saveMut.mutate({
              main_api: form.main_api,
              model: form.model || undefined,
              temperature: Number(form.temperature) || undefined,
              max_context: Number(form.max_context) || undefined,
              max_tokens: Number(form.max_tokens) || undefined,
            })
          }
        >
          {t("sillytavern:common.save")}
        </Button>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2 max-w-2xl">
        <label className="block">
          <span className="text-xs font-medium text-muted">{t("sillytavern:model.mainApi")}</span>
          <Select
            value={form.main_api}
            onChange={(e) => setForm((f) => ({ ...f, main_api: e.target.value }))}
            className="mt-1 w-full"
          >
            {MAIN_APIS.map((api) => (
              <option key={api} value={api}>
                {api}
              </option>
            ))}
          </Select>
        </label>
        <label className="block">
          <span className="text-xs font-medium text-muted">{t("sillytavern:model.model")}</span>
          <Input
            value={form.model}
            onChange={(e) => setForm((f) => ({ ...f, model: e.target.value }))}
            className="mt-1"
          />
        </label>
        <label className="block">
          <span className="text-xs font-medium text-muted">
            {t("sillytavern:model.temperature")}
          </span>
          <Input
            type="number"
            step="0.05"
            min="0"
            value={form.temperature}
            onChange={(e) => setForm((f) => ({ ...f, temperature: e.target.value }))}
            className="mt-1"
          />
        </label>
        <label className="block">
          <span className="text-xs font-medium text-muted">
            {t("sillytavern:model.maxContext")}
          </span>
          <Input
            type="number"
            value={form.max_context}
            onChange={(e) => setForm((f) => ({ ...f, max_context: e.target.value }))}
            className="mt-1"
          />
        </label>
        <label className="block">
          <span className="text-xs font-medium text-muted">{t("sillytavern:model.maxTokens")}</span>
          <Input
            type="number"
            value={form.max_tokens}
            onChange={(e) => setForm((f) => ({ ...f, max_tokens: e.target.value }))}
            className="mt-1"
          />
        </label>
      </div>
      </Card>
      </div>
  );
}
