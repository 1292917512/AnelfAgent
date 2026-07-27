/**
 * web 实体自定义面板 — 搜索配置与统计。
 *
 * 通过 scripts/link_entity_panels.py 软链接到前端 panels 目录，
 * Vite import.meta.glob 自动发现并懒加载。
 */
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { entitiesApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { StatusDot } from "@/components/common/StatusDot";
import { Save } from "lucide-react";

export default function WebPanel() {
  const queryClient = useQueryClient();
  const [apiKey, setApiKey] = useState("");
  const [proxy, setProxy] = useState("");

  const { data: config } = useQuery({
    queryKey: ["entity-config", "web"],
    queryFn: () => entitiesApi.config("web").then((r) => r.data),
  });

  // 初始化表单值
  const values = (config as Record<string, unknown>)?.values as Record<string, unknown> | undefined;

  const saveMutation = useMutation({
    mutationFn: () =>
      entitiesApi.updateConfigBatch("web", {
        baidu_api_key: apiKey || values?.baidu_api_key || "",
        web_proxy: proxy || values?.web_proxy || "",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["entity-config", "web"] });
    },
  });

  return (
    <div className="space-y-4 max-w-2xl">
      <Card title="百度搜索配置">
        <div className="space-y-3">
          <div>
            <label className="text-xs font-medium text-foreground block mb-1">API Key</label>
            <input
              type="password"
              value={apiKey || String(values?.baidu_api_key ?? "")}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="输入百度搜索 API Key"
              className="w-full px-2 py-1.5 rounded-md border border-border bg-elevated text-xs text-foreground font-mono"
            />
          </div>
          <div>
            <label className="text-xs font-medium text-foreground block mb-1">代理</label>
            <input
              type="text"
              value={proxy || String(values?.web_proxy ?? "")}
              onChange={(e) => setProxy(e.target.value)}
              placeholder="http://127.0.0.1:7890（可选）"
              className="w-full px-2 py-1.5 rounded-md border border-border bg-elevated text-xs text-foreground font-mono"
            />
          </div>
          <button
            onClick={() => saveMutation.mutate()}
            disabled={saveMutation.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-accent text-white text-xs font-medium hover:opacity-90 disabled:opacity-50"
          >
            <Save size={12} />
            保存
          </button>
        </div>
      </Card>

      <Card title="安全">
        <div className="flex items-center gap-2 text-xs">
          <StatusDot status="ok" />
          <span className="text-foreground">SSRF 防护</span>
          <span className="text-muted">
            {String(values?.web_ssrf_protection ?? "true") === "true" ? "已开启" : "已关闭"}
          </span>
        </div>
      </Card>
    </div>
  );
}
