import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { audioApi } from "@/lib/api";
import { speakerListQuery } from "@/lib/identity-queries";
import { useIdentityFilters } from "@/components/identity/useIdentityFilters";
import { toast } from "@/stores/toast-store";
import type { SpeakerListItem } from "./types";

/** 身份列表的查询、筛选和操作状态；弹窗草稿由既有表单组件持有。 */
export function useSpeakers() {
  const { t } = useTranslation("sound");
  const queryClient = useQueryClient();
  const filtersUI = useIdentityFilters();
  const [detailId, setDetailId] = useState<number | null>(null);
  const [confirmTarget, setConfirmTarget] = useState<SpeakerListItem | null>(null);
  const [mergeSource, setMergeSource] = useState<SpeakerListItem | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<SpeakerListItem | null>(null);
  const [enrollOpen, setEnrollOpen] = useState(false);
  const [pruneOpen, setPruneOpen] = useState(false);
  const [consolidateOpen, setConsolidateOpen] = useState(false);
  const [pruneInsignificant, setPruneInsignificant] = useState(true);
  const [simmapOpen, setSimmapOpen] = useState(false);

  const { data, isLoading, isFetching, isPlaceholderData, error, refetch } = useQuery(speakerListQuery(filtersUI.filters));

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["audioSpeakers"] });


  const confirmMutation = useMutation({
    mutationFn: ({ id, name, role }: { id: number; name: string; role: string }) =>
      audioApi.confirmSpeaker(id, name, role),
    onSuccess: () => {
      toast.success(t("messages.confirmSuccess"));
      setConfirmTarget(null);
      invalidate();
    },
  });

  const mergeMutation = useMutation({
    mutationFn: (targetId: number) => audioApi.mergeSpeakers(mergeSource!.id, targetId),
    onSuccess: () => {
      toast.success(t("messages.mergeSuccess"));
      setMergeSource(null);
      invalidate();
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => audioApi.deleteSpeaker(deleteTarget!.id),
    onSuccess: () => {
      toast.success(t("messages.deleteSuccess"));
      setDeleteTarget(null);
      invalidate();
    },
  });

  const enrollMutation = useMutation({
    mutationFn: ({ name, role, file }: { name: string; role: string; file: File }) =>
      audioApi.enrollAudio(file, name, role),
    onSuccess: (r) => {
      toast.success(t("messages.enrollSuccess", { count: r.data.samples_enrolled }));
      setEnrollOpen(false);
      invalidate();
    },
  });

  const items = data?.items ?? [];
  const mergeFilters = useIdentityFilters();
  const mergeQuery = useQuery({ ...speakerListQuery(mergeFilters.filters), enabled: mergeSource !== null });
  const mergeCandidates = (mergeQuery.data?.items ?? []).filter((s) => s.id !== mergeSource?.id);
  // 归类：待确认（需处理）在前，已确认在后
  const pendingItems = items.filter((s) => s.status === "pending");
  const confirmedItems = items.filter((s) => s.status === "confirmed");

  const pruneMutation = useMutation({
    mutationFn: () => audioApi.pruneSpeakers(true),
    onSuccess: (r) => {
      toast.success(t("messages.pruneSuccess", { count: r.data.pruned }));
      setPruneOpen(false);
      invalidate();
    },
  });

  const consolidatePreview = useMutation({
    mutationFn: () => audioApi.consolidateSpeakers({ dry_run: true }),
  });

  const refineMut = useMutation({
    mutationFn: (id: number) => audioApi.refineSpeaker(id),
    onSuccess: (r) => {
      toast.success(t("messages.refineSuccess", {
        samples: r.data.samples,
        drift: r.data.anchor_similarity ?? t("messages.refineInitial"),
      }));
      invalidate();
    },
  });

  const consolidateRun = useMutation({
    mutationFn: () =>
      audioApi.consolidateSpeakers({
        dry_run: false,
        prune_insignificant: pruneInsignificant,
      }),
    onSuccess: (r) => {
      toast.success(t("messages.consolidateSuccess", {
        clusters: r.data.merges.length,
        count: r.data.speakers_affected + r.data.pruned.length,
      }));
      setConsolidateOpen(false);
      consolidatePreview.reset();
      invalidate();
    },
  });

  return {
    ...filtersUI, detailId, setDetailId, confirmTarget, setConfirmTarget, mergeSource,
    setMergeSource, deleteTarget, setDeleteTarget, enrollOpen, setEnrollOpen, pruneOpen,
    setPruneOpen, consolidateOpen, setConsolidateOpen, pruneInsignificant, setPruneInsignificant, simmapOpen,
    setSimmapOpen, invalidate, confirmMutation, mergeMutation, deleteMutation, enrollMutation,
    items, mergeFilters, mergeQuery, mergeCandidates, pendingItems, confirmedItems,
    pruneMutation, consolidatePreview, refineMut, consolidateRun, data, isLoading,
    isFetching, isPlaceholderData, error, refetch,
  };
}
