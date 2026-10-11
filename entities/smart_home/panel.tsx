/**
 * 智能家居实体面板 — 薄壳：连接摘要条 + 三个模块页签。
 *
 * 页签即模块边界：设备（房间分组实时控制）/ 连接（外部接入平台组件卡）/
 * 组件与注入（设备域组件管理 + AI 注入预览）。数据与 SSE 实时推送收口在
 * panels/useSmartHomeData，各页签为独立子面板组件。
 *
 * i18n：locales/{zh,en}.json 由 lib/entity-plugin-locales.ts 启动时 eager
 * 注册（命名空间 smart_home）；_registry.groups/configSections 声明
 * 工具页与配置中心的分组名。
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Boxes, Home, PlugZap } from "lucide-react";
import { Card } from "@/components/common/Card";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { Badge, Button, LoadingBlock } from "@/components/ui";
import { DevicesPanel } from "./panels/DevicesPanel";
import { DomainsPanel } from "./panels/DomainsPanel";
import { ProvidersPanel } from "./panels/ProvidersPanel";
import { useSmartHomeData } from "./panels/useSmartHomeData";

type SmartHomeTab = "devices" | "providers" | "domains";

export default function SmartHomePanel() {
  const { t } = useTranslation("smart_home");
  const data = useSmartHomeData();
  const [tab, setTab] = useState<SmartHomeTab>("devices");

  if (data.isError) {
    return (
      <Card>
        <div className="flex flex-col items-center gap-3 py-8 text-center">
          <AlertTriangle className="h-6 w-6 text-danger" />
          <p className="text-sm text-muted">{t("loadFailed")}</p>
          <Button size="sm" onClick={data.refreshAll}>
            {t("retry")}
          </Button>
        </div>
      </Card>
    );
  }

  if (data.isLoading || !data.status) {
    return <LoadingBlock />;
  }

  // 模块页签按激活态显示：设备（有连接/设备）、连接（恒在）、组件与注入（已配置且有设备或注入）
  const showDevices = data.status.connected || data.devices.length > 0;
  const showDomains =
    data.status.configured &&
    (data.devices.length > 0 || Boolean(data.preview?.injecting));
  const TABS: TabItem<SmartHomeTab>[] = [
    ...(showDevices
      ? [{ key: "devices" as const, label: t("tabs.devices"), icon: Home }]
      : []),
    { key: "providers" as const, label: t("tabs.providers"), icon: PlugZap },
    ...(showDomains
      ? [{ key: "domains" as const, label: t("tabs.domains"), icon: Boxes }]
      : []),
  ];
  const activeTab: SmartHomeTab = TABS.some((item) => item.key === tab)
    ? tab
    : TABS[0]?.key ?? "providers";

  return (
    <div className="space-y-4">
      {/* 连接摘要条：各平台状态 + 设备总数 + 注入开关一目了然 */}
      <div className="flex flex-wrap items-center gap-2">
        {data.status.providers.map((provider) => (
          <Badge
            key={provider.provider}
            variant={
              provider.connected ? "ok" : provider.configured ? "warn" : "neutral"
            }
          >
            <span className="h-1.5 w-1.5 rounded-full bg-current" />
            {provider.provider_name}
          </Badge>
        ))}
        <Badge variant="accent">
          {t("summary.devices", { count: data.status.device_count })}
        </Badge>
        <Badge variant={data.preview?.injecting ? "accent2" : "neutral"}>
          {data.preview?.injecting ? t("summary.injecting") : t("summary.injectOff")}
        </Badge>
      </div>

      <TabBar tabs={TABS} activeTab={activeTab} onChange={setTab} />

      {activeTab === "devices" && (
        <DevicesPanel
          devices={data.devices}
          domains={data.domains}
          domainByHa={data.domainByHa}
          connected={data.status.connected}
          pendingIds={data.pendingIds}
          onControl={data.control}
        />
      )}
      {activeTab === "providers" && (
        <ProvidersPanel status={data.status} onChanged={data.refreshAll} />
      )}
      {activeTab === "domains" && (
        <DomainsPanel
          domains={data.domains}
          preview={data.preview}
          onRefreshPreview={data.refreshPreview}
          onSaveConfig={data.saveConfig}
        />
      )}
    </div>
  );
}
