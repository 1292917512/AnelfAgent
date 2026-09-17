import { useState } from "react";
import { useTranslation } from "react-i18next";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { PageContainer, PageHeader } from "@/components/common/PageContainer";
import { AudioLines, AudioWaveform, Clock3, Mic, LayoutGrid, Search, Sparkles, Upload, Users } from "lucide-react";
import { AudioOverviewPanel } from "@/pages/sound/OverviewPanel";
import { VoiceSessionPanel } from "@/pages/sound/VoiceSessionPanel";
import { SpeakersPanel } from "@/pages/sound/SpeakersPanel";
import { TimelinePanel } from "@/pages/sound/TimelinePanel";
import { TranscriptsPanel } from "@/pages/sound/TranscriptsPanel";
import { IdentifyPanel } from "@/pages/sound/IdentifyPanel";
import { SoundGenerationPanel } from "@/pages/sound/GenerationPanel";
import { VoicePresetPanel } from "@/pages/sound/VoicePresetPanel";

type SoundTab = "overview" | "presets" | "generation" | "speakers" | "timeline" | "transcripts" | "identify" | "voice";

/** 声音能力 — 核心能力页（/sound）：实时对话 + 音色预设库 + 生成能力链 + 声纹身份 + 话语时间线/检索编辑 + 识别入库 + 语音会话 */
export default function Sound() {
  const { t } = useTranslation("sound");
  const [tab, setTab] = useState<SoundTab>("overview");

  const TABS: TabItem<SoundTab>[] = [
    { key: "overview", label: t("tabs.overview"), icon: LayoutGrid },
    { key: "presets", label: t("tabs.presets"), icon: AudioWaveform },
    { key: "generation", label: t("tabs.generation"), icon: Sparkles },
    { key: "speakers", label: t("tabs.speakers"), icon: Users },
    { key: "timeline", label: t("tabs.timeline"), icon: Clock3 },
    { key: "transcripts", label: t("tabs.transcripts"), icon: Search },
    { key: "identify", label: t("tabs.identify"), icon: Upload },
    { key: "voice", label: t("tabs.voice"), icon: Mic },
  ];

  return (
    <PageContainer>
      <PageHeader
        icon={<AudioLines size={20} className="text-accent" />}
        title={t("title")}
        subtitle={t("subtitle")}
      />
      <TabBar tabs={TABS} activeTab={tab} onChange={setTab} />
      {tab === "overview" && <AudioOverviewPanel />}
      {tab === "presets" && <VoicePresetPanel />}
      {tab === "generation" && <SoundGenerationPanel />}
      {tab === "speakers" && <SpeakersPanel />}
      {tab === "timeline" && <TimelinePanel />}
      {tab === "transcripts" && <TranscriptsPanel />}
      {tab === "identify" && <IdentifyPanel />}
      {tab === "voice" && <VoiceSessionPanel />}
    </PageContainer>
  );
}
