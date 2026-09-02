import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Play, Plus, Users } from "lucide-react";
import { apiErrorMessage, sillytavernApi } from "./api";
import type { StCharacter, StCharacterCreatePayload } from "./types";
import {
  Button,
  EmptyState,
  Input,
  LoadingBlock,
  Modal,
  Switch,
  Textarea,
  toast,
} from "@/components/ui";
import { Badge } from "@/components/ui";

interface FormState {
  name: string;
  description: string;
  personality: string;
  first_mes: string;
  scenario: string;
  mes_example: string;
  system_prompt: string;
  tags: string;
}

const EMPTY_FORM: FormState = {
  name: "",
  description: "",
  personality: "",
  first_mes: "",
  scenario: "",
  mes_example: "",
  system_prompt: "",
  tags: "",
};

/** 新建/编辑角色卡弹窗：编辑模式按字段逐个提交（edit 接口为单字段） */
export function CharacterFormModal({
  open,
  onClose,
  editing,
}: {
  open: boolean;
  onClose: () => void;
  editing: StCharacter | null;
}) {
  const { t } = useTranslation(["sillytavern", "common"]);
  const queryClient = useQueryClient();
  const [form, setForm] = useState<FormState>(EMPTY_FORM);

  useEffect(() => {
    if (!open) return;
    if (editing) {
      void sillytavernApi
        .character(editing.avatar)
        .then((r) => {
          const c = r.data;
          setForm({
            name: c.name ?? "",
            description: c.description ?? "",
            personality: c.personality ?? "",
            first_mes: c.first_mes ?? "",
            scenario: (c as unknown as StCharacterCreatePayload).scenario ?? "",
            mes_example: (c as unknown as StCharacterCreatePayload).mes_example ?? "",
            system_prompt: (c as unknown as StCharacterCreatePayload).system_prompt ?? "",
            tags: (c.tags ?? []).join(", "),
          });
        })
        .catch((err) =>
          toast.error(apiErrorMessage(err, t("sillytavern:common.requestFailed"))),
        );
    } else {
      setForm(EMPTY_FORM);
    }
  }, [open, editing, t]);

  const createMut = useMutation({
    mutationFn: (data: StCharacterCreatePayload) =>
      sillytavernApi.createCharacter(data),
    onSuccess: () => {
      toast.success(t("sillytavern:characters.created"));
      queryClient.invalidateQueries({ queryKey: ["st", "characters"] });
      onClose();
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("sillytavern:common.requestFailed"))),
  });

  const editMut = useMutation({
    mutationFn: async () => {
      const avatar = editing!.avatar;
      const fields: Array<[string, string]> = [
        ["description", form.description],
        ["personality", form.personality],
        ["first_mes", form.first_mes],
        ["scenario", form.scenario],
        ["mes_example", form.mes_example],
        ["system_prompt", form.system_prompt],
      ];
      await Promise.all(
        fields.map(([field, value]) =>
          sillytavernApi.editCharacter({
            avatar,
            field,
            value,
            current_name: editing!.name,
          }),
        ),
      );
      if (form.name !== editing!.name) {
        await sillytavernApi.editCharacter({
          avatar,
          field: "name",
          value: form.name,
          current_name: editing!.name,
        });
      }
      await sillytavernApi.editCharacter({
        avatar,
        field: "tags",
        value: form.tags
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean)
          .join(","),
        current_name: editing!.name,
      });
    },
    onSuccess: () => {
      toast.success(t("sillytavern:characters.updated"));
      queryClient.invalidateQueries({ queryKey: ["st", "characters"] });
      onClose();
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("sillytavern:common.requestFailed"))),
  });

  const pending = createMut.isPending || editMut.isPending;

  const set = (key: keyof FormState) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));

  const handleSubmit = () => {
    if (!form.name.trim()) {
      toast.error(t("sillytavern:characters.nameRequired"));
      return;
    }
    const tags = form.tags
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    if (editing) {
      editMut.mutate();
    } else {
      createMut.mutate({
        name: form.name.trim(),
        description: form.description,
        personality: form.personality,
        first_mes: form.first_mes,
        scenario: form.scenario,
        mes_example: form.mes_example,
        system_prompt: form.system_prompt,
        tags,
      });
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      width="max-w-2xl"
      title={
        editing
          ? t("sillytavern:characters.editTitle")
          : t("sillytavern:characters.createTitle")
      }
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose}>
            {t("sillytavern:common.cancel")}
          </Button>
          <Button variant="primary" size="sm" loading={pending} onClick={handleSubmit}>
            {t("sillytavern:common.save")}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <label className="block">
          <span className="text-xs font-medium text-muted">
            {t("sillytavern:characters.fieldName")}
          </span>
          <Input value={form.name} onChange={set("name")} className="mt-1" />
        </label>
        <label className="block">
          <span className="text-xs font-medium text-muted">
            {t("sillytavern:characters.fieldDescription")}
          </span>
          <Textarea rows={4} value={form.description} onChange={set("description")} className="mt-1" />
        </label>
        <label className="block">
          <span className="text-xs font-medium text-muted">
            {t("sillytavern:characters.fieldPersonality")}
          </span>
          <Textarea rows={2} value={form.personality} onChange={set("personality")} className="mt-1" />
        </label>
        <label className="block">
          <span className="text-xs font-medium text-muted">
            {t("sillytavern:characters.fieldFirstMes")}
          </span>
          <Textarea rows={3} value={form.first_mes} onChange={set("first_mes")} className="mt-1" />
        </label>
        <label className="block">
          <span className="text-xs font-medium text-muted">
            {t("sillytavern:characters.fieldScenario")}
          </span>
          <Textarea rows={2} value={form.scenario} onChange={set("scenario")} className="mt-1" />
        </label>
        <label className="block">
          <span className="text-xs font-medium text-muted">
            {t("sillytavern:characters.fieldMesExample")}
          </span>
          <Textarea rows={3} value={form.mes_example} onChange={set("mes_example")} className="mt-1" />
        </label>
        <label className="block">
          <span className="text-xs font-medium text-muted">
            {t("sillytavern:characters.fieldSystemPrompt")}
          </span>
          <Textarea rows={2} value={form.system_prompt} onChange={set("system_prompt")} className="mt-1" />
        </label>
        <label className="block">
          <span className="text-xs font-medium text-muted">
            {t("sillytavern:characters.fieldTags")}
          </span>
          <Input
            value={form.tags}
            onChange={set("tags")}
            placeholder={t("sillytavern:characters.placeholderTags")}
            className="mt-1"
          />
        </label>
      </div>
    </Modal>
  );
}

/** 角色卡面板：网格卡片 + 新建/编辑/删除；酒馆未运行时显示引导启动 */
export function CharactersPanel() {
  const { t } = useTranslation(["sillytavern", "common"]);
  const queryClient = useQueryClient();

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<StCharacter | null>(null);
  const [deleting, setDeleting] = useState<StCharacter | null>(null);
  const [deleteChats, setDeleteChats] = useState(false);

  const { data: status } = useQuery({
    queryKey: ["st", "status"],
    queryFn: () => sillytavernApi.status().then((r) => r.data),
    refetchInterval: 15_000,
  });

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["st", "characters"],
    queryFn: () => sillytavernApi.characters().then((r) => r.data),
    enabled: !!status?.running,
    retry: false,
  });

  const deleteMut = useMutation({
    mutationFn: (payload: { avatar: string; delete_chats: boolean }) =>
      sillytavernApi.deleteCharacter(payload),
    onSuccess: () => {
      toast.success(t("sillytavern:characters.deleted"));
      queryClient.invalidateQueries({ queryKey: ["st", "characters"] });
      setDeleting(null);
      setDeleteChats(false);
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("sillytavern:common.requestFailed"))),
  });

  if (isLoading) return <LoadingBlock label={t("common:loading")} />;

  // 酒馆未运行：引导启动
  if (status && !status.running) {
    return (
      <EmptyState
        icon={Play}
        title={t("sillytavern:common.notRunning")}
        action={
          <Button variant="primary" size="sm" onClick={() => refetch()}>
            <Play size={14} />
            {t("sillytavern:overview.start")}
          </Button>
        }
      />
    );
  }

  if (isError) {
    return (
      <EmptyState
        icon={Users}
        title={t("sillytavern:characters.loadFailed")}
        action={
          <Button variant="secondary" size="sm" onClick={() => refetch()}>
            {t("sillytavern:common.retry")}
          </Button>
        }
      />
    );
  }

  const characters = data?.characters ?? [];

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-sm text-muted">
          {t("sillytavern:characters.count", { count: data?.count ?? characters.length })}
        </span>
        <Button
          variant="primary"
          size="sm"
          onClick={() => {
            setEditing(null);
            setFormOpen(true);
          }}
        >
          <Plus size={15} />
          {t("sillytavern:characters.createTitle")}
        </Button>
      </div>

      {characters.length === 0 ? (
        <EmptyState
          icon={Users}
          title={t("sillytavern:characters.emptyTitle")}
          description={t("sillytavern:characters.emptyHint")}
        />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {characters.map((c) => (
            <div
              key={c.avatar}
              className="rounded-lg border border-border bg-card p-4 shadow-sm transition-all duration-200 hover:border-border-strong hover:shadow-md animate-rise flex flex-col gap-2"
            >
              <div className="flex items-start justify-between gap-2">
                <span className="font-semibold text-heading truncate" title={c.name}>
                  {c.name}
                </span>
                {c.fav && <Badge variant="warn">{t("sillytavern:characters.fav")}</Badge>}
              </div>
              <p className="text-xs text-muted line-clamp-3 whitespace-pre-wrap">
                {c.description?.trim() || t("sillytavern:characters.noDescription")}
              </p>
              {c.tags?.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {c.tags.slice(0, 6).map((tag) => (
                    <Badge key={tag} variant="accent">
                      {tag}
                    </Badge>
                  ))}
                  {c.tags.length > 6 && <Badge variant="neutral">+{c.tags.length - 6}</Badge>}
                </div>
              )}
              <div className="mt-auto pt-2 flex items-center gap-2 justify-end">
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => {
                    setEditing(c);
                    setFormOpen(true);
                  }}
                >
                  {t("sillytavern:common.edit")}
                </Button>
                <Button variant="danger" size="sm" onClick={() => setDeleting(c)}>
                  {t("sillytavern:common.delete")}
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      <CharacterFormModal open={formOpen} onClose={() => setFormOpen(false)} editing={editing} />

      <Modal
        open={!!deleting}
        onClose={() => setDeleting(null)}
        width="max-w-sm"
        title={t("sillytavern:characters.deleteTitle")}
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => setDeleting(null)}>
              {t("sillytavern:common.cancel")}
            </Button>
            <Button
              variant="danger"
              size="sm"
              loading={deleteMut.isPending}
              onClick={() =>
                deleting && deleteMut.mutate({ avatar: deleting.avatar, delete_chats: deleteChats })
              }
            >
              {t("sillytavern:common.delete")}
            </Button>
          </>
        }
      >
        <p className="text-sm text-foreground">
          {deleting
            ? t("sillytavern:characters.deleteMessage", { name: deleting.name })
            : ""}
        </p>
        <label className="mt-3 flex items-center gap-2 text-sm text-muted">
          <Switch checked={deleteChats} onChange={setDeleteChats} />
          {t("sillytavern:characters.alsoDeleteChats")}
        </label>
      </Modal>
    </div>
  );
}
