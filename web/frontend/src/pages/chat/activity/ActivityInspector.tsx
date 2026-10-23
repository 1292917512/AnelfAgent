import { PopoverContent } from "@/components/ui/PopoverContent";
import { useEffect, useRef, useState, type ReactNode } from "react";
import * as Popover from "@radix-ui/react-popover";
import { Expand } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router-dom";
import { Modal } from "@/components/ui";
import { cn } from "@/lib/utils";

/** 鼠标悬停预览、点击完整阅读；触屏与键盘直接打开同一详情。 */
export function ActivityInspector({ title, preview, children, trigger, className }: {
  title: string; preview: ReactNode; children: ReactNode; trigger?: ReactNode; className?: string;
}) {
  const { t } = useTranslation("workbench");
  const [hovered, setHovered] = useState(false);
  const { key: locationKey } = useLocation();
  const [openedAt, setOpenedAt] = useState<string | null>(null);
  const open = openedAt === locationKey;
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const clearTimer = () => { clearTimeout(timer.current); };
  const hidePreview = () => { clearTimer(); timer.current = setTimeout(() => setHovered(false), 120); };
  useEffect(() => () => clearTimeout(timer.current), []);
  const label = t("activity.inspect", { title });
  return <>
    <Popover.Root open={hovered && !open} onOpenChange={setHovered}>
      <Popover.Anchor asChild>
        <button type="button" aria-label={label} aria-haspopup="dialog" className={cn("activity-inspect", className)}
          onPointerEnter={(event) => {
            if (event.pointerType !== "mouse" || !window.matchMedia("(hover: hover)").matches) return;
            clearTimer(); timer.current = setTimeout(() => setHovered(true), 350);
          }}
          onPointerLeave={hidePreview}
          onBlur={() => { clearTimer(); setHovered(false); }}
          onClick={() => { clearTimer(); setHovered(false); setOpenedAt(locationKey); }}>
          {trigger ?? <Expand size={14} aria-hidden="true" />}
        </button>
      </Popover.Anchor>

        <PopoverContent side="top" align="end" sideOffset={8} collisionPadding={16}
          className="activity-hover-card" aria-label={t("activity.preview", { title })}
          aria-hidden={!hovered || open ? true : undefined}
          onPointerEnter={clearTimer} onPointerLeave={hidePreview}
          onOpenAutoFocus={(event) => event.preventDefault()} onCloseAutoFocus={(event) => event.preventDefault()}>
          <p className="activity-hover-title">{title}</p>
          <div className="activity-hover-content">{preview}</div>
          <p className="activity-note">{t("activity.previewHint")}</p>
        </PopoverContent>

    </Popover.Root>
    <Modal open={open} onClose={() => setOpenedAt(null)} title={title} width="max-w-3xl">
      <div className="activity-inspection-content">{children}</div>
    </Modal>
  </>;
}
