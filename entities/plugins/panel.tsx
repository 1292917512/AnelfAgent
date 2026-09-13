/**
 * 插件管理实体面板 — 已装插件管理 / 市场订阅 / 浏览安装。
 *
 * 经 module-links.mjs 代码生成接入（@entities 别名直引实体目录），
 * Vite import.meta.glob 自动发现并懒加载；数据面走 /api/entity/plugins
 * （entities/plugins/router.py），与 AI 管理工具共用同一插件引擎。
 */
import { useQuery } from "@tanstack/react-query";
import { pluginsApi } from "./panels/api";
import { InstalledSection } from "./panels/InstalledSection";
import { MarketplaceSection } from "./panels/MarketplaceSection";
import { BrowseSection } from "./panels/BrowseSection";

export default function PluginsPanel() {
  const { data: plugins } = useQuery({
    queryKey: ["plugins-installed"],
    queryFn: () => pluginsApi.list().then((r) => r.data),
  });

  return (
    <div className="space-y-4 max-w-4xl">
      <InstalledSection plugins={plugins ?? []} />
      <MarketplaceSection />
      <BrowseSection />
    </div>
  );
}
