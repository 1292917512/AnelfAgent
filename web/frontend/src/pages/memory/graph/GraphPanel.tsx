/** 关系图谱面板：全图拓扑 + 过滤 + 图例 + 节点抽屉 + 关系增删改。 */

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Plus, RefreshCw, Search } from "lucide-react";
import "@xyflow/react/dist/style.css";

import { graphApi } from "@/lib/api";
import type { GraphEdge, GraphNode } from "@/lib/types";
import { Badge, Button, EmptyState, Input, LoadingBlock, Select } from "@/components/ui";
import { GraphCanvas } from "./GraphCanvas";
import { GraphNodeDrawer } from "./GraphNodeDrawer";
import { NODE_TYPE_COLORS } from "./GraphNodeCard";
import { RelationFormModal } from "./RelationFormModal";

export function GraphPanel() {
  const { t } = useTranslation("graph");
  const queryClient = useQueryClient();
  const [predicate, setPredicate] = useState("");
  const [origin, setOrigin] = useState("");
  const [search, setSearch] = useState("");
  const [selectedNodeId, setSelectedNodeId] = useState<number | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingEdge, setEditingEdge] = useState<GraphEdge | null>(null);
  const [presetSubject, setPresetSubject] = useState("");

  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ["graph", predicate, origin],
    queryFn: () => graphApi.get({
      predicate: predicate || undefined,
      origin: origin || undefined,
    }).then((r) => r.data),
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["graph"] });
    queryClient.invalidateQueries({ queryKey: ["graph-node-detail"] });
  };

  const addEdgeMutation = useMutation({ mutationFn: graphApi.addEdge, onSuccess: invalidate });
  const updateEdgeMutation = useMutation({
    mutationFn: ({ id, ...form }: { id: number } & Parameters<typeof graphApi.updateEdge>[1]) =>
      graphApi.updateEdge(id, form),
    onSuccess: invalidate,
  });
  const deleteEdgeMutation = useMutation({ mutationFn: graphApi.deleteEdge, onSuccess: invalidate });
  const deleteNodeMutation = useMutation({
    mutationFn: graphApi.deleteNode,
    onSuccess: () => { setSelectedNodeId(null); invalidate(); },
  });
  const renameNodeMutation = useMutation({
    mutationFn: ({ nodeKey, label }: { nodeKey: string; label: string }) =>
      graphApi.upsertNode({ node_key: nodeKey, label }),
    onSuccess: invalidate,
  });
  const mergeNodeMutation = useMutation({
    mutationFn: ({ source, target }: { source: string; target: string }) =>
      graphApi.mergeNodes(source, target),
    onSuccess: () => { setSelectedNodeId(null); invalidate(); },
  });

  const openAddModal = (preset = "") => {
    setEditingEdge(null);
    setPresetSubject(preset);
    setModalOpen(true);
  };
  const openEditModal = (edge: GraphEdge) => {
    setEditingEdge(edge);
    setModalOpen(true);
  };

  const filtered = useMemo(() => {
    if (!data || !search.trim()) return data;
    const kw = search.trim().toLowerCase();
    const hitIds = new Set(
      data.nodes
        .filter((n) => n.node_key.toLowerCase().includes(kw) || n.label.toLowerCase().includes(kw))
        .map((n) => n.id),
    );
    // 搜索命中节点 + 其一跳邻居，保持上下文可见
    for (const edge of data.edges) {
      if (hitIds.has(edge.subject.id) || hitIds.has(edge.object.id)) {
        hitIds.add(edge.subject.id);
        hitIds.add(edge.object.id);
      }
    }
    return {
      ...data,
      nodes: data.nodes.filter((n) => hitIds.has(n.id)),
      edges: data.edges.filter((e) => hitIds.has(e.subject.id) && hitIds.has(e.object.id)),
    };
  }, [data, search]);

  const selectedNode: GraphNode | null = useMemo(
    () => data?.nodes.find((n) => n.id === selectedNodeId) ?? null,
    [data, selectedNodeId],
  );
  const selectedEdges = useMemo(
    () => (data?.edges ?? []).filter(
      (e) => e.subject.id === selectedNodeId || e.object.id === selectedNodeId,
    ),
    [data, selectedNodeId],
  );

  if (isLoading) return <LoadingBlock />;
  if (!data || data.nodes.length === 0) {
    return (
      <div className="py-10">
        <EmptyState title={t("empty.title")} description={t("empty.description")} />
        <div className="mt-4 flex justify-center">
          <Button variant="primary" onClick={() => openAddModal()}>
            <Plus size={14} /> {t("toolbar.addRelation")}
          </Button>
        </div>
        <RelationFormModal open={modalOpen} onClose={() => setModalOpen(false)}
          edge={editingEdge} presetSubject={presetSubject}
          onSubmit={async (form) => { await addEdgeMutation.mutateAsync(form); }} />
      </div>
    );
  }

  const presentTypes = Object.keys(data.stats.node_types);

  return (
    <div className="flex h-[calc(100vh-220px)] flex-col gap-3">
      {/* 工具栏 */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
          <Input className="w-52 pl-8" placeholder={t("toolbar.searchHint")}
            value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        <Select value={predicate} onChange={(e) => setPredicate(e.target.value)}>
          <option value="">{t("toolbar.allPredicates")}</option>
          {Object.keys(data.stats.top_predicates).map((p) => (
            <option key={p} value={p}>{p}</option>
          ))}
        </Select>
        <Select value={origin} onChange={(e) => setOrigin(e.target.value)}>
          <option value="">{t("toolbar.allOrigins")}</option>
          <option value="manual_tool">{t("origins.manual_tool")}</option>
          <option value="heartbeat_extract">{t("origins.heartbeat_extract")}</option>
          <option value="web_ui">{t("origins.web_ui")}</option>
        </Select>
        <Badge>{t("toolbar.stats", { nodes: data.stats.nodes, edges: data.stats.edges })}</Badge>
        <div className="ml-auto flex items-center gap-2">
          <Button variant="secondary" onClick={() => refetch()} disabled={isFetching}>
            <RefreshCw size={14} className={isFetching ? "animate-spin" : ""} /> {t("toolbar.refresh")}
          </Button>
          <Button variant="primary" onClick={() => openAddModal()}>
            <Plus size={14} /> {t("toolbar.addRelation")}
          </Button>
        </div>
      </div>

      {/* 画布 */}
      <div className="min-h-0 flex-1 overflow-hidden rounded-md border border-border bg-card">
        <GraphCanvas data={filtered ?? data} selectedNodeId={selectedNodeId}
          onNodeClick={(id) => setSelectedNodeId(id)}
          onEdgeClick={(edgeId) => {
            const edge = data.edges.find((e) => e.id === edgeId);
            if (edge) openEditModal(edge);
          }} />
      </div>

      {/* 图例 + 提示 */}
      <div className="flex flex-wrap items-center gap-3 text-xs text-muted">
        {presentTypes.map((type) => (
          <span key={type} className="flex items-center gap-1">
            <span className={`inline-block h-2.5 w-2.5 rounded-full border ${NODE_TYPE_COLORS[type]?.border ?? "border-slate-500/60"} ${NODE_TYPE_COLORS[type]?.bg ?? "bg-slate-500/15"}`} />
            {t(`nodeTypes.${type}`, { defaultValue: type })}
          </span>
        ))}
        <span className="ml-auto">{t("hint")}</span>
      </div>

      {/* 节点详情抽屉（含实体画像联动） */}
      <GraphNodeDrawer
        node={selectedNode}
        edges={selectedEdges}
        onClose={() => setSelectedNodeId(null)}
        onAddRelation={(preset) => openAddModal(preset)}
        onEditEdge={openEditModal}
        onDeleteEdge={(id) => deleteEdgeMutation.mutate(id)}
        onDeleteNode={(key) => deleteNodeMutation.mutate(key)}
        onRenameNode={(key, label) => renameNodeMutation.mutate({ nodeKey: key, label })}
        onMergeNode={(source, target) => mergeNodeMutation.mutate({ source, target })}
      />

      <RelationFormModal open={modalOpen} onClose={() => setModalOpen(false)}
        edge={editingEdge} presetSubject={presetSubject}
        onSubmit={async (form) => {
          if (editingEdge) {
            await updateEdgeMutation.mutateAsync({
              id: editingEdge.id,
              predicate: form.predicate, strength: form.strength,
              evidence: form.evidence, symmetric: form.symmetric,
            });
          } else {
            await addEdgeMutation.mutateAsync(form);
          }
        }} />
    </div>
  );
}
