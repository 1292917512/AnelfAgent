import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { KeyRound, RefreshCw } from "lucide-react";
import { difyApi } from "./api";
import type { DifyProvider } from "./types";
import { Card } from "@/components/common/Card";
import { StatusDot } from "@/components/common/StatusDot";
import { Button, Modal, Textarea, toast } from "@/components/ui";
import { apiErrorMessage } from "@/lib/api";

export function ModelsTab() {
  const { t } = useTranslation("dify");
  const queryClient = useQueryClient();
  const [target, setTarget] = useState<DifyProvider | null>(null);
  const [credJson, setCredJson] = useState("");

  const { data, refetch } = useQuery({
    queryKey: ["dify-providers"],
    queryFn: () => difyApi.listProviders().then((r) => r.data),
    retry: false,
  });

  const mutation = useMutation({
    mutationFn: () => difyApi.setCredential(target!.provider, JSON.parse(credJson)),
    onSuccess: (resp) => {
      toast.success(resp.data?.message || t("messages.actionDone"));
      setTarget(null);
      setCredJson("");
      queryClient.invalidateQueries({ queryKey: ["dify-providers"] });
    },
    onError: (err: unknown) => toast.error(apiErrorMessage(err, t("messages.actionFailed"))),
  });

  const submit = () => {
    try {
      JSON.parse(credJson);
    } catch {
      toast.error(t("models.credInvalid"));
      return;
    }
    mutation.mutate();
  };

  const providers = data?.providers ?? [];

  return (
    <div className="space-y-4 max-w-3xl">
      <Card
        title={t("models.title")}
        subtitle={t("models.desc")}
        actions={
          <button
            onClick={() => refetch()}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-elevated text-muted hover:bg-hover transition-all"
          >
            <RefreshCw size={14} /> {t("actions.refresh")}
          </button>
        }
      >
        {providers.length === 0 ? (
          <div className="py-10 text-center text-sm text-muted">{t("models.empty")}</div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {providers.map((p) => (
              <div
                key={p.provider}
                className="flex items-center gap-2 rounded-md border border-border bg-elevated px-3 py-2"
              >
                <StatusDot status={p.has_credential ? "ok" : "offline"} />
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-heading truncate">{p.label || p.provider}</div>
                  <div className="text-xs text-muted truncate">{p.provider}</div>
                </div>
                <button
                  onClick={() => {
                    setTarget(p);
                    setCredJson("");
                  }}
                  title={t("models.configure")}
                  className="p-1.5 rounded-md text-muted hover:text-accent hover:bg-hover transition-all"
                >
                  <KeyRound size={15} />
                </button>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Modal
        open={target !== null}
        onClose={() => setTarget(null)}
        title={target ? t("models.credTitle", { name: target.label || target.provider }) : ""}
        footer={
          <div className="flex justify-end gap-2">
            <Button variant="secondary" size="sm" onClick={() => setTarget(null)}>
              {t("actions.cancel")}
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={submit}
              loading={mutation.isPending}
              disabled={!credJson.trim()}
            >
              {t("actions.save")}
            </Button>
          </div>
        }
      >
        <div className="space-y-3">
          <p className="text-xs text-muted">{t("models.credDesc", { provider: target?.provider ?? "" })}</p>
          <Textarea
            value={credJson}
            onChange={(e) => setCredJson(e.target.value)}
            rows={6}
            className="font-mono text-xs"
            placeholder='{"openai_api_key": "sk-..."}'
          />
        </div>
      </Modal>
    </div>
  );
}
