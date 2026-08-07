/** 图谱节点详情抽屉：节点信息 + 实体画像联动 + 关系管理。 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Check, GitMerge, Pencil, Plus, Trash2 } from "lucide-react";

import { graphApi } from "@/lib/api";
import type { GraphEdge, GraphNode } from "@/lib/types";
import { Button, Input } from "@/components/ui";
import { Drawer } from "@/components/common/Drawer";

interface Props {
  node: GraphNode | null;
  edges: GraphEdge[];
  onClose: () => void;
  onAddRelation: (presetSubject: string) => void;
  onEditEdge: (edge: GraphEdge) => void;
  onDeleteEdge: (edgeId: number) => void;
  onDeleteNode: (nodeKey: string) => void;
  onRenameNode: (nodeKey: string, label: string) => void;
  onMergeNode: (sourceKey: string, targetKey: string) => void;
}

export function GraphNodeDrawer({
  node, edges, onClose, onAddRelation, onEditEdge, onDeleteEdge,
  onDeleteNode, onRenameNode, onMergeNode,
}: Props) {
  const { t } = useTranslation("graph");
  const [editingLabel, setEditingLabel] = useState(false);
  const [labelDraft, setLabelDraft] = useState("");
  const [mergeOpen, setMergeOpen] = useState(false);
  const [mergeTarget, setMergeTarget] = useState("");

  const isEntity = node?.node_type === "user" || node?.node_type === "group";
  const { data: detail } = useQuery({
    queryKey: ["graph-node-detail", node?.node_key],
    queryFn: () => graphApi.nodeDetail(node!.node_key).then((r) => r.data),
    enabled: Boolean(node && isEntity),
  });

  return (
    <Drawer open={node !== null} onClose={onClose}
      title={node ? (node.label || node.node_key) : ""}>
      {node && (
        <div className="space-y-4">
          {/* 节点信息 + 展示名编辑 */}
          <div className="space-y-1 text-sm">
            <div><span className="text-muted">{t("drawer.nodeKey")}：</span>{node.node_key}</div>
            <div><span className="text-muted">{t("drawer.nodeType")}：</span>
              {t(`nodeTypes.${node.node_type}`, { defaultValue: node.node_type })}</div>
            <div className="flex items-center gap-2">
              <span className="text-muted">{t("drawer.label")}：</span>
              {editingLabel ? (
                <>
                  <Input className="h-7 w-40" value={labelDraft}
                    onChange={(e) => setLabelDraft(e.target.value)} />
                  <button className="text-ok hover:underline"
                    onClick={() => { onRenameNode(node.node_key, labelDraft.trim()); setEditingLabel(false); }}>
                    <Check size={14} />
                  </button>
                </>
              ) : (
                <>
                  <span className="text-heading">{node.label || "—"}</span>
                  <button className="text-muted hover:text-heading"
                    onClick={() => { setLabelDraft(node.label); setEditingLabel(true); }}>
                    <Pencil size={12} />
                  </button>
                </>
              )}
            </div>
          </div>

          {/* 实体画像联动（user/group 节点） */}
          {isEntity && detail?.found && (
            <div className="rounded-md border border-border p-2">
              <div className="mb-1 text-xs font-medium text-muted">
                {t("drawer.profileTitle")}
                {detail.profile ? ` · ${t("drawer.convCount", { count: detail.profile.conv_num })}` : ""}
              </div>
              {detail.profile?.personality ? (
                <div className="max-h-48 overflow-y-auto whitespace-pre-wrap text-xs text-heading">
                  {detail.profile.personality}
                </div>
              ) : (
                <div className="text-xs text-muted">{t("drawer.noProfile")}</div>
              )}
              {detail.primary && detail.primary !== node.node_key && (
                <div className="mt-1 text-xs text-muted">
                  {t("drawer.primary")}：{detail.primary}
                </div>
              )}
              {detail.aliases && detail.aliases.length > 0 && (
                <div className="mt-1 text-xs text-muted">
                  {t("drawer.aliases")}：{detail.aliases.join("、")}
                </div>
              )}
            </div>
          )}

          {/* 操作 */}
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="secondary" onClick={() => onAddRelation(node.node_key)}>
              <Plus size={13} /> {t("drawer.addFromHere")}
            </Button>
            <Button size="sm" variant="secondary" onClick={() => setMergeOpen((v) => !v)}>
              <GitMerge size={13} /> {t("drawer.merge")}
            </Button>
            <Button size="sm" variant="danger" onClick={() => onDeleteNode(node.node_key)}>
              <Trash2 size={13} /> {t("drawer.deleteNode")}
            </Button>
          </div>
          {mergeOpen && (
            <div className="flex items-center gap-2">
              <Input className="h-8 flex-1" placeholder={t("drawer.mergeHint")}
                value={mergeTarget} onChange={(e) => setMergeTarget(e.target.value)} />
              <Button size="sm" variant="primary" disabled={!mergeTarget.trim()}
                onClick={() => { onMergeNode(node.node_key, mergeTarget.trim()); setMergeOpen(false); setMergeTarget(""); }}>
                {t("drawer.mergeConfirm")}
              </Button>
            </div>
          )}

          {/* 关系列表 */}
          <div>
            <div className="mb-2 text-sm font-medium text-heading">
              {t("drawer.relations", { count: edges.length })}
            </div>
            <div className="space-y-2">
              {edges.map((edge) => (
                <div key={edge.id} className="rounded-md border border-border p-2 text-sm">
                  <div className="text-heading">
                    {(edge.subject.label || edge.subject.node_key)}
                    <span className="mx-1 text-accent">
                      {edge.symmetric ? `─[${edge.predicate}]─` : `─[${edge.predicate}]→`}
                    </span>
                    {(edge.object.label || edge.object.node_key)}
                  </div>
                  <div className="mt-1 flex items-center gap-2 text-xs text-muted">
                    <span>{t("drawer.strength")} {edge.strength.toFixed(2)}</span>
                    <span>{t(`origins.${edge.origin}`, { defaultValue: edge.origin })}</span>
                    <span className="ml-auto flex gap-2">
                      <button className="text-accent hover:underline" onClick={() => onEditEdge(edge)}>
                        {t("drawer.edit")}
                      </button>
                      <button className="text-danger hover:underline" onClick={() => onDeleteEdge(edge.id)}>
                        {t("drawer.delete")}
                      </button>
                    </span>
                  </div>
                  {edge.evidence && <div className="mt-1 text-xs text-muted">{edge.evidence}</div>}
                </div>
              ))}
              {edges.length === 0 && (
                <div className="text-sm text-muted">{t("drawer.noRelations")}</div>
              )}
            </div>
          </div>
        </div>
      )}
    </Drawer>
  );
}
