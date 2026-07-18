import { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { configMetaApi, type ConfigMetaItem } from "@/lib/api";
import { PageContainer, PageHeader } from "@/components/common/PageContainer";
import { cn } from "@/lib/utils";
import { SlidersHorizontal, RotateCcw, Check, Loader2 } from "lucide-react";

/** 配置项分组展示顺序（未列出的组排最后） */
const GROUP_ORDER = [
  "AnelfAgent/Mind",
  "AnelfAgent/Mind/Memory",
  "记忆",
  "工具门控",
  "错误守卫",
  "安全",
  "Prompt缓存",
  "上下文压缩",
  "技能",
  "子代理",
];

export default function Config() {
  const { t } = useTranslation(["config", "common"]);
  const queryClient = useQueryClient();
  const [activeGroup, setActiveGroup] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["configMeta"],
    queryFn: () => configMetaApi.list().then((r) => r.data),
  });

  const groups = useMemo(() => {
    const list = data?.groups ?? [];
    return [...list].sort((a, b) => {
      const ia = GROUP_ORDER.indexOf(a.group);
      const ib = GROUP_ORDER.indexOf(b.group);
      return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
    });
  }, [data]);

  const current = groups.find((g) => g.group === activeGroup) ?? groups[0];

  return (
    <PageContainer>
      <PageHeader
        icon={<SlidersHorizontal size={22} />}
        title={t("title")}
        subtitle={t("subtitle")}
      />

      {/* 分组 Tab */}
      <div className="flex gap-1.5 overflow-x-auto pb-1 -mx-1 px-1">
        {groups.map((g) => (
          <button
            key={g.group}
            onClick={() => setActiveGroup(g.group)}
            className={cn(
              "shrink-0 px-3 py-1.5 text-sm font-medium rounded-[var(--radius-md)] border transition-all",
              (current?.group === g.group)
                ? "bg-[var(--accent-subtle)] text-[var(--accent)] border-[var(--accent)]"
                : "text-[var(--muted)] border-[var(--border)] hover:text-[var(--text)] hover:border-[var(--border-strong)]",
            )}
          >
            {t(`groups.${g.group}`, { defaultValue: g.group })}
          </button>
        ))}
      </div>

      {/* 配置项列表 */}
      {isLoading ? (
        <div className="flex justify-center py-12 text-[var(--muted)]">
          <Loader2 size={24} className="animate-spin" />
        </div>
      ) : (
        <div className="grid gap-2.5">
          {current?.items.map((item) => (
            <ConfigItemRow key={item.key} item={item} onSaved={() => queryClient.invalidateQueries({ queryKey: ["configMeta"] })} />
          ))}
        </div>
      )}
    </PageContainer>
  );
}

function ConfigItemRow({ item, onSaved }: { item: ConfigMetaItem; onSaved: () => void }) {
  const { t } = useTranslation("config");
  const [value, setValue] = useState<unknown>(item.value);
  const [saved, setSaved] = useState(false);

  const mutation = useMutation({
    mutationFn: (v: unknown) => configMetaApi.save(item.key, v),
    onSuccess: () => {
      setSaved(true);
      onSaved();
      setTimeout(() => setSaved(false), 1500);
    },
  });

  const dirty = JSON.stringify(value) !== JSON.stringify(item.value);
  const isDefault = JSON.stringify(value) === JSON.stringify(item.default);

  const save = (v: unknown) => {
    setValue(v);
    mutation.mutate(v);
  };

  return (
    <div className="flex items-center gap-3 p-3 rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--card)]">
      <div className="flex-1 min-w-0">
        <div className="text-sm text-[var(--text-strong)]">{item.description}</div>
        <div className="text-xs text-[var(--muted)] font-mono truncate">{item.key}</div>
      </div>

      <div className="flex items-center gap-2 shrink-0">
        {/* 类型适配控件 */}
        {item.type === "boolean" ? (
          <button
            role="switch"
            aria-checked={!!value}
            disabled={!item.editable || mutation.isPending}
            onClick={() => save(!value)}
            className={cn(
              "w-11 h-6 rounded-full transition-colors relative",
              value ? "bg-[var(--accent)]" : "bg-[var(--secondary)]",
              "disabled:opacity-50",
            )}
          >
            <span
              className={cn(
                "absolute top-0.5 w-5 h-5 rounded-full bg-white transition-transform",
                value ? "translate-x-[22px]" : "translate-x-0.5",
              )}
            />
          </button>
        ) : item.type === "enum" && item.options ? (
          <select
            value={String(value ?? "")}
            disabled={!item.editable || mutation.isPending}
            onChange={(e) => save(e.target.value)}
            className="bg-[var(--bg)] border border-[var(--input)] rounded-[var(--radius-md)] px-2.5 py-1.5 text-sm text-[var(--text)] outline-none focus:border-[var(--ring)]"
          >
            {item.options.map((opt) => (
              <option key={opt} value={opt}>{opt}</option>
            ))}
          </select>
        ) : (
          <input
            type={item.type === "integer" || item.type === "float" ? "number" : "text"}
            value={value === null || value === undefined ? "" : String(value)}
            disabled={!item.editable}
            onChange={(e) => {
              const raw = e.target.value;
              if (item.type === "integer") setValue(raw === "" ? "" : parseInt(raw, 10));
              else if (item.type === "float") setValue(raw === "" ? "" : parseFloat(raw));
              else setValue(raw);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && dirty && !mutation.isPending) mutation.mutate(value);
            }}
            className="w-36 bg-[var(--bg)] border border-[var(--input)] rounded-[var(--radius-md)] px-2.5 py-1.5 text-sm text-[var(--text)] outline-none focus:border-[var(--ring)] disabled:opacity-50"
          />
        )}

        {/* 非布尔类型：保存按钮 */}
        {item.type !== "boolean" && dirty && (
          <button
            onClick={() => mutation.mutate(value)}
            disabled={mutation.isPending}
            className="px-3 py-1.5 text-sm font-medium rounded-[var(--radius-md)] bg-[var(--accent)] text-[var(--primary-foreground)] hover:bg-[var(--accent-hover)] disabled:opacity-50 transition-all"
          >
            {mutation.isPending ? <Loader2 size={14} className="animate-spin" /> : t("common:save")}
          </button>
        )}

        {/* 保存成功反馈 */}
        {saved && <Check size={16} className="text-[var(--success)]" />}

        {/* 重置为默认值 */}
        {!isDefault && item.editable && (
          <button
            title={t("resetToDefault")}
            onClick={() => save(item.default)}
            disabled={mutation.isPending}
            className="p-1.5 rounded-[var(--radius-md)] text-[var(--muted)] hover:text-[var(--text)] hover:bg-[var(--bg-hover)] transition-colors disabled:opacity-50"
          >
            <RotateCcw size={14} />
          </button>
        )}
      </div>
    </div>
  );
}
