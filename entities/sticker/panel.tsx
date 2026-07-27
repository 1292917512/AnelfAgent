/**
 * sticker 实体自定义面板 — 表情包管理。
 *
 * 通过 scripts/link_entity_panels.py 软链接到前端 panels 目录。
 */
import { useQuery } from "@tanstack/react-query";
import api from "@/lib/api";
import { Card } from "@/components/common/Card";

interface StickerStats {
  total: number;
  indexed: number;
}

export default function StickerPanel() {
  const { data: stats } = useQuery({
    queryKey: ["sticker-stats"],
    queryFn: () => api.get<StickerStats>("/stickers/stats").then((r: { data: StickerStats }) => r.data),
  });

  return (
    <div className="space-y-4 max-w-2xl">
      <Card title="表情包统计">
        <div className="grid grid-cols-2 gap-4 text-center">
          <div>
            <p className="text-2xl font-bold text-heading">{stats?.total ?? "—"}</p>
            <p className="text-[10px] text-muted mt-1">总数</p>
          </div>
          <div>
            <p className="text-2xl font-bold text-heading">{stats?.indexed ?? "—"}</p>
            <p className="text-[10px] text-muted mt-1">已索引</p>
          </div>
        </div>
      </Card>

      <Card title="模糊匹配">
        <p className="text-xs text-muted">
          模糊匹配阈值和自动回复配置请在「配置」标签页中调整。
        </p>
      </Card>
    </div>
  );
}
