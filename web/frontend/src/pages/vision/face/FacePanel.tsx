import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { faceApi, type FaceStatus } from "@/lib/api";
import { Badge, Button, LoadingBlock, toast } from "@/components/ui";
import { ScanFace, ServerCog, Users, Activity, Database } from "lucide-react";
import { PersonsView } from "./PersonsView";
import { EventsView } from "./EventsView";

type FaceView = "overview" | "persons" | "events";

const SUBTABS: { key: FaceView; icon: typeof Users }[] = [
  { key: "overview", icon: ScanFace },
  { key: "persons", icon: Users },
  { key: "events", icon: Activity },
];

/** 人脸识别面板：引擎总览 + 人物档案管理 + 出现事件时间线。 */
export function FacePanel() {
  const { t } = useTranslation("vision");
  const [view, setView] = useState<FaceView>("overview");

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-1 border-b border-border">
        {SUBTABS.map(({ key, icon: Icon }) => (
          <button
            key={key}
            onClick={() => setView(key)}
            className={`flex items-center gap-1.5 px-3 py-2 text-sm border-b-2 -mb-px transition-colors ${
              view === key
                ? "border-accent text-accent font-medium"
                : "border-transparent text-muted hover:text-foreground"
            }`}
          >
            <Icon size={14} />
            {t(`face.tabs.${key}`)}
          </button>
        ))}
      </div>
      {view === "overview" && <OverviewView />}
      {view === "persons" && <PersonsView />}
      {view === "events" && <EventsView />}
    </div>
  );
}

function OverviewView() {
  const { t } = useTranslation("vision");
  const queryClient = useQueryClient();

  const { data: status, isLoading } = useQuery({
    queryKey: ["faceStatus"],
    queryFn: () => faceApi.status().then((r) => r.data),
    refetchInterval: 15_000,
  });

  const recheck = useMutation({
    mutationFn: () => faceApi.status(true).then((r) => r.data),
    onSuccess: (data: FaceStatus) => {
      queryClient.setQueryData(["faceStatus"], data);
      toast[data.engine.reachable ? "success" : "error"](
        data.engine.reachable ? t("face.engine.reachable") : t("face.engine.unreachable"),
      );
    },
  });

  if (isLoading || !status) return <LoadingBlock label={t("common:loading")} />;

  const { engine, stats, thresholds } = status;
  const health = engine.health;

  return (
    <div className="space-y-4 max-w-4xl">
      {/* 识别引擎卡片（只读状态；地址配置在 模型页 → 组件凭据） */}
      <div className="rounded-md border border-border bg-card p-4 space-y-2">
        <div className="flex items-center gap-2">
          <ServerCog size={15} className="text-accent" />
          <span className="text-sm font-semibold text-heading">{t("face.engine.title")}</span>
          <Badge variant={engine.reachable ? "ok" : engine.configured ? "danger" : "neutral"}>
            {engine.reachable
              ? t("face.engine.reachable")
              : engine.configured ? t("face.engine.unreachable") : t("face.engine.notConfigured")}
          </Badge>
          <Button
            size="sm"
            className="ml-auto"
            loading={recheck.isPending}
            onClick={() => recheck.mutate()}
          >
            {t("face.engine.recheck")}
          </Button>
        </div>
        <div className="flex items-center gap-2 flex-wrap text-xs">
          <span className="text-muted">{t("face.engine.desc")}</span>
          <a href="/webui/models" className="text-accent hover:underline">
            {t("face.engine.goConfigure")}
          </a>
        </div>
        {engine.endpoint && (
          <p className="text-[11px] font-mono text-muted break-all">{engine.endpoint}</p>
        )}
        {health && (
          <div className="flex flex-wrap gap-2 text-[11px]">
            <Badge variant="neutral">{t("face.engine.model")}: {health.model || "-"}</Badge>
            <Badge variant="neutral">{t("face.engine.dim")}: {health.dim || "-"}</Badge>
            <Badge variant="neutral">{t("face.engine.device")}: {health.device || "-"}</Badge>
          </div>
        )}
        <p className="text-[11px] text-muted">{t("face.engine.modelNote")}</p>
      </div>

      {/* 库统计 */}
      <div className="rounded-md border border-border bg-card p-4 space-y-3">
        <div className="flex items-center gap-2">
          <Database size={15} className="text-accent" />
          <span className="text-sm font-semibold text-heading">{t("face.stats.title")}</span>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <Stat label={t("face.stats.persons")} value={stats.persons ?? 0} />
          <Stat label={t("face.stats.pending")} value={stats.pending_persons ?? 0} warn />
          <Stat label={t("face.stats.bound")} value={stats.bound_persons ?? 0} />
          <Stat label={t("face.stats.samples")} value={stats.samples ?? 0} />
          <Stat label={t("face.stats.events")} value={stats.events ?? 0} />
          <Stat label={t("face.stats.unread")} value={stats.unread_events ?? 0} warn />
          <Stat label={t("face.stats.dims")} value={stats.face_dims ?? 0} />
        </div>
        <div className="flex flex-wrap gap-2 text-[11px] text-muted pt-1 border-t border-border">
          <span>{t("face.stats.matchThreshold")}: {thresholds.match}</span>
          <span>{t("face.stats.mergeThreshold")}: {thresholds.merge}</span>
          <span>{t("face.stats.separation")}: {thresholds.separation}</span>
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, warn }: { label: string; value: number; warn?: boolean }) {
  return (
    <div className="rounded-md bg-elevated px-3 py-2">
      <div className={`text-lg font-semibold ${warn && value > 0 ? "text-warn" : "text-heading"}`}>
        {value}
      </div>
      <div className="text-[11px] text-muted">{label}</div>
    </div>
  );
}
