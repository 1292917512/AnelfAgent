import { PageContainer } from "@/components/common/PageContainer";
import { StickersPanel } from "@/pages/stickers/StickersPanel";

/** 独立表情包页（/stickers，向后兼容）— 功能已并入「数据管理」页 */
export default function Stickers() {
  return (
    <PageContainer>
      <StickersPanel />
    </PageContainer>
  );
}
