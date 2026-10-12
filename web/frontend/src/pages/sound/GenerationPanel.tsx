/** 声音生成能力面板：合成/音色管理/音乐的能力优先级链（音色预设归「音色」页签）。 */
import { useQuery } from "@tanstack/react-query";
import { AsyncState } from "@/components/common/AsyncState";
import { audioApi } from "@/lib/api";
import { CapabilityChainPanel } from "@/components/common/CapabilityChainPanel";

const CAPS = ["tts", "voice_mgmt", "music"];

export function SoundGenerationPanel() {
  const query = useQuery({
    queryKey: ["soundCapabilities"], throwOnError: false,
    queryFn: () => audioApi.capabilities().then((r) => r.data),
  });

  const status = query.data;

  return (
    <AsyncState pending={query.isPending} error={query.error} retry={() => void query.refetch()}>
      {status &&
      <CapabilityChainPanel
        status={status}
        caps={CAPS}
        configKey="sound_provider_priority"
        ns="sound"
        queryKey="soundCapabilities"
      />}
    </AsyncState>
  );
}
