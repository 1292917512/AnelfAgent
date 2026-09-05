import { registerPluginI18n } from "@/lib/plugin-i18n";
import zh from "./plugins/locales/zh.json";
import en from "./plugins/locales/en.json";

registerPluginI18n("plugins", { zh, en });

/**
 * 插件管理实体面板 — 已装插件管理 / 市场订阅 / 浏览安装。
 *
 * 通过 scripts/link_entity_panels.py 软链接到前端 panels 目录，
 * Vite import.meta.glob 自动发现并懒加载；数据面走 /api/entity/plugins
 * （entities/plugins/router.py），与 AI 管理工具共用同一插件引擎。
 */
import { useQuery } from "@tanstack/react-query";
import { pluginsApi } from "./plugins/api";
import { InstalledSection } from "./plugins/InstalledSection";
import { MarketplaceSection } from "./plugins/MarketplaceSection";
import { BrowseSection } from "./plugins/BrowseSection";

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
