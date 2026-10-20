import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { faceApi } from "@/lib/api";
import { personListQuery } from "@/lib/identity-queries";
import { useIdentityFilters } from "@/components/identity/useIdentityFilters";
import { toast } from "@/stores/toast-store";
import type { FacePerson } from "@/lib/types";

/** 身份列表的查询、筛选和操作状态；弹窗草稿由既有表单组件持有。 */
export function usePersons() {
  const { t } = useTranslation("vision");
  const queryClient = useQueryClient();
  const filtersUI = useIdentityFilters();
  const [detailId, setDetailId] = useState<number | null>(null);
  const [confirmTarget, setConfirmTarget] = useState<FacePerson | null>(null);
  const [mergeSource, setMergeSource] = useState<FacePerson | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<FacePerson | null>(null);
  const [bindTarget, setBindTarget] = useState<FacePerson | null>(null);
  const [bindScope, setBindScope] = useState("");
  const [enrollOpen, setEnrollOpen] = useState(false);
  const [enrollScope, setEnrollScope] = useState("");
  const [identifyOpen, setIdentifyOpen] = useState(false);
  const [identifyFile, setIdentifyFile] = useState<File | null>(null);
  const [identifyIngest, setIdentifyIngest] = useState(true);
  const [pruneOpen, setPruneOpen] = useState(false);
  const [consolidateOpen, setConsolidateOpen] = useState(false);
  const [pruneInsignificant, setPruneInsignificant] = useState(true);

  const { data, isLoading, isFetching, isPlaceholderData, error, refetch } = useQuery(personListQuery(filtersUI.filters));

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["facePersons"] });
    queryClient.invalidateQueries({ queryKey: ["faceStatus"] });
    queryClient.invalidateQueries({ queryKey: ["faceEvents"] });
  };


  const confirmMutation = useMutation({
    mutationFn: ({ id, name, role }: { id: number; name: string; role: string }) =>
      faceApi.confirmPerson(id, name, role),
    onSuccess: () => {
      toast.success(t("face.messages.confirmSuccess"));
      setConfirmTarget(null);
      invalidate();
    },
  });

  const mergeMutation = useMutation({
    mutationFn: (targetId: number) => faceApi.mergePersons(mergeSource!.id, targetId),
    onSuccess: () => {
      toast.success(t("face.messages.mergeSuccess"));
      setMergeSource(null);
      invalidate();
    },
  });

  const bindMutation = useMutation({
    mutationFn: () => faceApi.bindPerson(bindTarget!.id, bindScope),
    onSuccess: () => {
      toast.success(t("face.messages.bindSuccess"));
      setBindTarget(null); setBindScope("");
      invalidate();
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => faceApi.deletePerson(deleteTarget!.id),
    onSuccess: () => {
      toast.success(t("face.messages.deleteSuccess"));
      setDeleteTarget(null);
      invalidate();
    },
  });

  const enrollMutation = useMutation({
    mutationFn: ({ name, role, file }: { name: string; role: string; file: File }) =>
      faceApi.enrollImage(file, name, { role, entityScope: enrollScope }),
    onSuccess: () => {
      toast.success(t("face.messages.enrollSuccess"));
      setEnrollOpen(false); setEnrollScope("");
      invalidate();
    },
  });

  const identifyMutation = useMutation({
    mutationFn: () => faceApi.identifyImage(identifyFile!, identifyIngest),
    onSuccess: (r) => {
      const faces = r.data.faces?.length ?? r.data.hits?.length ?? 0;
      toast.success(t("face.messages.identifySuccess", { count: faces }));
      if (identifyIngest) {
        setIdentifyOpen(false); setIdentifyFile(null);
        invalidate();
      }
    },
  });

  const pruneMutation = useMutation({
    mutationFn: () => faceApi.prunePersons(true),
    onSuccess: (r) => {
      toast.success(t("face.messages.pruneSuccess", { count: r.data.pruned }));
      setPruneOpen(false);
      invalidate();
    },
  });

  const refineMut = useMutation({
    mutationFn: (id: number) => faceApi.refinePerson(id),
    onSuccess: (r) => {
      toast.success(t("face.messages.refineSuccess", {
        samples: r.data.samples,
        drift: r.data.anchor_similarity ?? t("face.messages.refineInitial"),
      }));
      invalidate();
    },
  });

  const consolidatePreview = useMutation({
    mutationFn: () => faceApi.consolidatePersons({ dry_run: true }),
  });

  const consolidateRun = useMutation({
    mutationFn: () => faceApi.consolidatePersons({
      dry_run: false, prune_insignificant: pruneInsignificant }),
    onSuccess: (r) => {
      toast.success(t("face.messages.consolidateSuccess", {
        clusters: r.data.merges.length,
        count: r.data.persons_affected + r.data.pruned.length,
      }));
      setConsolidateOpen(false);
      consolidatePreview.reset();
      invalidate();
    },
  });

  const items = data?.items ?? [];
  const mergeFilters = useIdentityFilters();
  const mergeQuery = useQuery({ ...personListQuery(mergeFilters.filters), enabled: mergeSource !== null });
  const mergeCandidates = (mergeQuery.data?.items ?? []).filter((p) => p.id !== mergeSource?.id);
  const pendingItems = items.filter((p) => p.status === "pending");
  const confirmedItems = items.filter((p) => p.status === "confirmed");

  return {
    ...filtersUI, detailId, setDetailId, confirmTarget, setConfirmTarget, mergeSource,
    setMergeSource, deleteTarget, setDeleteTarget, bindTarget, setBindTarget, bindScope,
    setBindScope, enrollOpen, setEnrollOpen, enrollScope, setEnrollScope, identifyOpen,
    setIdentifyOpen, identifyFile, setIdentifyFile, identifyIngest, setIdentifyIngest, pruneOpen,
    setPruneOpen, consolidateOpen, setConsolidateOpen, pruneInsignificant, setPruneInsignificant, invalidate,
    confirmMutation, mergeMutation, bindMutation, deleteMutation, enrollMutation, identifyMutation,
    pruneMutation, refineMut, consolidatePreview, consolidateRun, items, mergeFilters,
    mergeQuery, mergeCandidates, pendingItems, confirmedItems, data, isLoading,
    isFetching, isPlaceholderData, error, refetch,
  };
}
