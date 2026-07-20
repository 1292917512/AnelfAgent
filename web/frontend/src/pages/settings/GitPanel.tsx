import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery } from "@tanstack/react-query";
import { TestTube } from "lucide-react";
import { systemApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { Button, LoadingBlock } from "@/components/ui";
import { InfoRow } from "./shared";

/** Git 配置面板：全局配置 + GitHub 连通性测试 */
export function GitPanel() {
  const { t } = useTranslation("settings");
  const [testResult, setTestResult] = useState<string>("");

  const { data: config } = useQuery({
    queryKey: ["gitConfig"],
    queryFn: () => systemApi.git().then((r) => r.data),
  });

  const testMutation = useMutation({
    mutationFn: () => systemApi.testGithub().then((r) => r.data),
    onSuccess: (data) => setTestResult(JSON.stringify(data, null, 2)),
  });

  return (
    <div className="space-y-4">
      <Card title={t("gitGlobalConfig")} actions={
        <Button variant="secondary" size="sm" onClick={() => testMutation.mutate()} loading={testMutation.isPending}>
          <TestTube size={14} /> {t("testGithub")}
        </Button>
      }>
        {config ? (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {Object.entries(config as Record<string, string>).map(([k, v]) => (
              <InfoRow key={k} label={<span className="font-mono">{k}</span>} value={v || "—"} mono={false} />
            ))}
          </div>
        ) : (
          <LoadingBlock />
        )}
      </Card>

      {testResult && (
        <Card title={t("githubConnectivity")}>
          <pre className="text-xs font-mono text-foreground bg-elevated border border-border rounded-md p-3 overflow-auto max-h-48">
            {testResult}
          </pre>
        </Card>
      )}
    </div>
  );
}
