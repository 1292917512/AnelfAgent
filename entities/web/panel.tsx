/**
 * web 实体自定义面板 — 抓取代理与安全配置。
 *
 * 通过 scripts/link_entity_panels.py 软链接到前端 panels 目录，
 * Vite import.meta.glob 自动发现并懒加载。
 */
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { configApi, entitiesApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { StatusDot } from "@/components/common/StatusDot";
import { Save } from "lucide-react";

export default function WebPanel() {
  const queryClient = useQueryClient();
  const [proxy, setProxy] = useState("");

  const { data: webTools } = useQuery({
    queryKey: ["webToolsConfig"],
    queryFn: () => configApi.getWebTools().then((r) => r.data),
  });

  const { data: entityConfig } = useQuery({
    queryKey: ["entity-config", "web"],
    queryFn: () => entitiesApi.config("web").then((r) => r.data),
  });

  // 初始化表单值
  const values = (entityConfig as Record<string, unknown>)?.values as Record<string, unknown> | undefined;

  const saveMutation = useMutation({
    mutationFn: () =>
      configApi.saveWebTools({ proxy: proxy || webTools?.proxy || "" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["webToolsConfig"] });
    },
  });

  return (
    <div className="space-y-4 max-w-2xl">
      <Card title="网页抓取代理">
        <div className="space-y-3">
          <div>
            <label className="text-xs font-medium text-foreground block mb-1">代理</label>
            <input
              type="text"
              value={proxy || String(webTools?.proxy ?? "")}
              onChange={(e) => setProxy(e.target.value)}
              placeholder="http://127.0.0.1:7890（可选，留空直连）"
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
