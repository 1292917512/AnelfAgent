import { useTranslation } from "react-i18next";
import { Download, Package, Settings2 } from "lucide-react";
import { PageContainer, PageIntro } from "@/components/common/PageContainer";
import { TabBar } from "@/components/common/TabBar";
import { useRouteTab } from "@/hooks/useRouteTab";
import { BrowseSection } from "./BrowseSection";
import { InstalledSection } from "./InstalledSection";
import { MarketplaceSection } from "./MarketplaceSection";

const TABS = ["installed", "browse", "marketplaces"] as const;

export default function PluginsPage() {
  const { t } = useTranslation("plugins");
  const [tab, setTab] = useRouteTab(TABS, "installed");
  return (
    <PageContainer>
      <PageIntro />
      <TabBar activeTab={tab} onChange={setTab} tabs={[
        { key: "installed", label: t("installed"), icon: Package },
        { key: "browse", label: t("browse"), icon: Download },
        { key: "marketplaces", label: t("sourceSettings"), icon: Settings2 },
      ]} />
      <div className="mt-4">
        {tab === "installed" && <InstalledSection onBrowse={() => setTab("browse")} />}
        {tab === "browse" && <BrowseSection onViewMarkets={() => setTab("marketplaces")} />}
        {tab === "marketplaces" && <MarketplaceSection />}
      </div>
    </PageContainer>
  );
}
