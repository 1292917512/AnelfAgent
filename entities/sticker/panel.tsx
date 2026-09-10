/**
 * sticker 实体自定义面板 — 表情包统计与配置指引（实体详情页 tab）。
 *
 * 完整的表情包库管理在「数据管理」页（复用 panels/library 组件群），
 * 本面板只呈现轻量统计；经 scripts/link_entity_panels.py 软链接到前端
 * panels 目录，i18n 由 entity-plugin-locales 启动时注册（ns: sticker）。
 */
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Card } from "@/components/common/Card";
import { stickersApi } from "./sticker/api";

export default function StickerPanel() {
  const { t } = useTranslation("sticker");
  const { data: stats } = useQuery({
    queryKey: ["sticker-stats"],
    queryFn: () => stickersApi.stats().then((r) => r.data),
  });

  return (
    <div className="space-y-4 max-w-2xl">
      <Card title={t("panel.statsTitle")}>
        <div className="grid grid-cols-2 gap-4 text-center">
          <div>
            <p className="text-2xl font-bold text-heading">{stats?.stickers ?? "—"}</p>
            <p className="text-[10px] text-muted mt-1">{t("panel.total")}</p>
          </div>
          <div>
            <p className="text-2xl font-bold text-heading">{stats?.indexed_images ?? "—"}</p>
            <p className="text-[10px] text-muted mt-1">{t("panel.indexed")}</p>
          </div>
        </div>
      </Card>

      <Card title={t("panel.fuzzyTitle")}>
        <p className="text-xs text-muted">{t("panel.fuzzyHint")}</p>
      </Card>
    </div>
  );
}
