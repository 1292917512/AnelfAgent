import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Brain, ChevronDown, ChevronRight, Database, GitBranch, Globe, Layers,
  MoreHorizontal, Package, Radio, Settings, Shield, Sparkles, Wrench, Zap,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { ConfigModuleNode } from "./configTree";

const MODULE_ICONS: Record<string, React.ComponentType<{ size?: number | string }>> = {
  mind: Brain,
  memory: Database,
  context: Layers,
  cache: Zap,
  tools: Wrench,
  security: Shield,
  skills: Sparkles,
  delegation: GitBranch,
  channel: Radio,
  network: Globe,
  system: Settings,
  entity: Package,
  other: MoreHorizontal,
};

interface ConfigSidebarProps {
  tree: ConfigModuleNode[];
  /** 当前选中分组（完整 group key） */
  active: string | null;
  onSelect: (group: string) => void;
}

/** 配置中心左侧模块树：模块（可折叠）→ 分组。 */
export function ConfigSidebar({ tree, active, onSelect }: ConfigSidebarProps) {
  const { t } = useTranslation("config");
  const activeModule = active?.includes("/") ? active.split("/")[0] : null;
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  return (
    <nav className="space-y-1">
      {tree.map((m) => {
        const Icon = MODULE_ICONS[m.module] ?? MoreHorizontal;
        // 单分组模块：点击模块名直接选中该分组，无需再展开一层
        const single = m.sections.length === 1 ? m.sections[0] : null;
        const isCollapsed = collapsed[m.module] ?? false;
        return (
          <div key={m.module}>
            <button
              type="button"
              onClick={() => {
                if (single) onSelect(single.group);
                else setCollapsed((c) => ({ ...c, [m.module]: !isCollapsed }));
              }}
              className={cn(
                "flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-sm font-medium transition-colors",
                activeModule === m.module
                  ? "text-accent"
                  : "text-heading hover:bg-hover",
              )}
            >
              <Icon size={16} />
              <span className="flex-1 text-left truncate">
                {t(`modules.${m.module}`, { defaultValue: m.module })}
              </span>
              {!single && (isCollapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />)}
            </button>

            {!single && !isCollapsed && (
              <div className="ml-4 mt-0.5 space-y-0.5 border-l border-border pl-2">
                {m.sections.map((s) => (
                  <button
                    key={s.group}
                    type="button"
                    onClick={() => onSelect(s.group)}
                    className={cn(
                      "flex w-full items-center justify-between gap-2 rounded-md px-2.5 py-1.5 text-[13px] transition-colors",
                      active === s.group
                        ? "bg-accent-subtle text-accent font-medium"
                        : "text-muted hover:text-foreground hover:bg-hover",
                    )}
                  >
                    <span className="truncate">
                      {t(`sections.${s.group}`, { defaultValue: s.group.split("/")[1] ?? s.group })}
                    </span>
                    <span className="text-[11px] text-muted/70 font-mono">{s.items.length}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </nav>
  );
}
