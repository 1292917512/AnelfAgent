/** 声音生成能力面板：合成/音色管理/音乐的能力优先级链（音色预设归「音色」页签）。 */
import { useQuery } from "@tanstack/react-query";
import { audioApi } from "@/lib/api";
import { CapabilityChainPanel } from "@/components/common/CapabilityChainPanel";

const CAPS = ["tts", "voice_mgmt", "music"];

export function SoundGenerationPanel() {
  const { data: status } = useQuery({
    queryKey: ["soundCapabilities"],
    queryFn: () => audioApi.capabilities().then((r) => r.data),
  });

  if (!status) return null;

  return (
    <div className="space-y-6 max-w-2xl">
      <CapabilityChainPanel
        status={status}
        caps={CAPS}
        configKey="sound_provider_priority"
        ns="sound"
        queryKey="soundCapabilities"
      />
    </div>
  );
}
