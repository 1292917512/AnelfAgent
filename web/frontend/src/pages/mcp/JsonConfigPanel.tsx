import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlignLeft, Save, ShieldAlert } from "lucide-react";
import { apiErrorMessage, mcpApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { Button, LoadingBlock, Textarea, toast } from "@/components/ui";

/** JSON 原始配置编辑面板：语法校验 + 格式化 + 安全提示 */
export function JsonConfigPanel() {
  const { t } = useTranslation("mcp");
  const queryClient = useQueryClient();
  const [content, setContent] = useState("");
  const [initialized, setInitialized] = useState(false);
  const [jsonError, setJsonError] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["mcpConfig"],
    queryFn: () => mcpApi.config().then((r) => r.data.content),
  });

  // 仅在首次加载后填充编辑器，后续编辑不被覆盖
  useEffect(() => {
    if (data != null && !initialized) {
      setContent(data);
      setInitialized(true);
    }
  }, [data, initialized]);

  const validate = (text: string): boolean => {
    try {
      JSON.parse(text);
      setJsonError("");
      return true;
    } catch (e) {
      setJsonError(t("invalidJson", { message: (e as Error).message }));
      return false;
    }
  };

  const handleChange = (text: string) => {
    setContent(text);
    if (jsonError) validate(text);
  };

  const handleFormat = () => {
    try {
      setContent(JSON.stringify(JSON.parse(content), null, 2));
      setJsonError("");
    } catch (e) {
      setJsonError(t("invalidJson", { message: (e as Error).message }));
    }
  };

  const saveMutation = useMutation({
    mutationFn: (json: string) => mcpApi.saveConfig(json),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["mcpServers"] });
      queryClient.invalidateQueries({ queryKey: ["mcpConfig"] });
      toast.success(t("toast.configSaved"));
    },
    onError: (err) => {
      toast.error(apiErrorMessage(err, t("toast.requestFailed")));
    },
  });

  const handleSave = () => {
    if (validate(content)) {
      saveMutation.mutate(content);
    }
  };

  if (isLoading) {
    return <LoadingBlock label={t("common:loading")} />;
  }

  return (
    <Card
      title={t("jsonConfig")}
      subtitle={t("jsonHint")}
      actions={
        <div className="flex gap-2">
          <Button variant="secondary" size="sm" onClick={handleFormat}>
            <AlignLeft size={14} />
            {t("format")}
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={handleSave}
            loading={saveMutation.isPending}
            disabled={!!jsonError}
          >
            <Save size={14} />
            {t("common:save")}
          </Button>
        </div>
      }
    >
      <div className="space-y-2">
        <div className="flex items-start gap-2 rounded-md border border-warn/30 bg-warn-subtle px-3 py-2 text-xs text-warn">
          <ShieldAlert size={14} className="mt-0.5 shrink-0" />
          {t("jsonSecretWarning")}
        </div>
        <Textarea
          value={content}
          onChange={(e) => handleChange(e.target.value)}
          onBlur={() => content && validate(content)}
          rows={18}
          className="font-mono text-xs"
          spellCheck={false}
        />
        {jsonError && <p className="text-xs text-danger break-all">{jsonError}</p>}
      </div>
    </Card>
  );
}
