/**
 * 频道前端插件注册表 — 频道卡片/路由的频道自定义 UI 由此驱动。
 *
 * 频道在 channels/<id>/frontend/ 内自带完整前端（组件/API/类型/locales），
 * 经 vite moduleFrontendsPlugin 软链到 src/plugins/channels/<id>/（软链提交 git），
 * 本模块通过 import.meta.glob 构建时自动发现。
 *
 * 插件 index.ts 为轻量清单模块：声明清单 + 自注册 i18n；组件经 loader
 * 动态 import 保持懒加载分块。删除频道目录即整体热拔出，核心零改动。
 *
 * 清单经 initChannelPlugins() 在应用渲染前逐个隔离加载：单插件模块级异常
 * 仅跳过该插件并记日志，不再拖垮整个入口 chunk（整站黑屏）。
 */
import { lazy, type ComponentType, type LazyExoticComponent } from "react";

/** 频道插件清单（index.ts 的默认导出） */
export interface ChannelPlugin {
  /** 登录组件（频道卡片头部的登录入口，如扫码/账密登录） */
  login?: () => Promise<{ default: ComponentType<{ compact?: boolean }> }>;
  /** 频道卡片展开区的自定义面板（如直播模式面板） */
  panel?: () => Promise<{ default: ComponentType }>;
  /** 整页路由（注册到 App 路由表，path 不含前导斜杠，如 "bilibili"） */
  route?: { path: string };
  /** 整页组件 loader（配合 route 使用） */
  page?: () => Promise<{ default: ComponentType }>;
  /** 从频道列表/测试面板隐藏（管理面另有入口的频道，如有整页路由的插件频道） */
  hiddenInChannelList?: boolean;
}

// 构建时解析：频道插件清单 loader（隔离加载，单插件失败不影响其余）
const pluginLoaders = import.meta.glob<{ default: ChannelPlugin }>(
  "../plugins/channels/*/index.ts",
);

const plugins = new Map<string, ChannelPlugin>();

// 组件映射由 initChannelPlugins 填充（lazy 组件为静态引用，渲染期零工厂调用，
// 满足 react-hooks/static-components；loader 仍为动态 import，保持懒加载分块）
export const CHANNEL_LOGIN_COMPONENTS: Record<
  string,
  LazyExoticComponent<ComponentType<{ compact?: boolean }>>
> = {};
export const CHANNEL_PANEL_COMPONENTS: Record<string, LazyExoticComponent<ComponentType>> = {};

/**
 * 应用渲染前加载全部频道插件清单（main.tsx 调用，幂等）。
 * 单插件模块级异常只跳过该插件，其余插件与核心 UI 不受影响。
 */
export async function initChannelPlugins(): Promise<void> {
  await Promise.all(
    Object.entries(pluginLoaders).map(async ([path, load]) => {
      const match = path.match(/\/plugins\/channels\/([^/]+)\/index\.ts$/);
      const key = match?.[1];
      if (!key) return;
      try {
        const mod = await load();
        plugins.set(key, mod.default);
      } catch (error) {
        console.error(`[channel-plugins] 频道插件加载失败已跳过: ${key}`, error);
      }
    }),
  );
  for (const [key, plugin] of plugins) {
    if (plugin.login) {
      CHANNEL_LOGIN_COMPONENTS[key] = lazy(
        plugin.login as () => Promise<{ default: ComponentType<{ compact?: boolean }> }>,
      );
    }
    if (plugin.panel) {
      CHANNEL_PANEL_COMPONENTS[key] = lazy(plugin.panel);
    }
  }
}

/** 获取频道插件清单（无插件或插件加载失败返回 undefined）。 */
export function getChannelPlugin(channelKey: string): ChannelPlugin | undefined {
  return plugins.get(channelKey);
}

/** 频道是否应从频道列表/测试面板隐藏。 */
export function isChannelHidden(channelKey: string): boolean {
  return plugins.get(channelKey)?.hiddenInChannelList === true;
}

/** 收集插件声明的整页路由。 */
export function listPluginRoutes(): Array<{ path: string; page: LazyExoticComponent<ComponentType> }> {
  const routes: Array<{ path: string; page: LazyExoticComponent<ComponentType> }> = [];
  for (const plugin of plugins.values()) {
    if (plugin.route && plugin.page) {
      routes.push({ path: plugin.route.path, page: lazy(plugin.page) });
    }
  }
  return routes;
}
