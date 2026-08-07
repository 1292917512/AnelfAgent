/** 关系图谱域类型（对应 agent/memory/graph 与 services/graph.py）。 */

export interface GraphNodeBrief {
  id: number;
  node_key: string;
  node_type: string;
  label: string;
}

export interface GraphNode extends GraphNodeBrief {
  metadata: Record<string, unknown>;
  created: number;
  updated: number;
  archived: boolean;
}

export interface GraphEdge {
  id: number;
  subject: GraphNodeBrief;
  predicate: string;
  object: GraphNodeBrief;
  symmetric: boolean;
  strength: number;
  evidence: string;
  source_memory_id: number | null;
  origin: string;
  metadata: Record<string, unknown>;
  created: number;
  updated: number;
  archived: boolean;
}

export interface GraphStats {
  nodes: number;
  edges: number;
  node_types: Record<string, number>;
  top_predicates: Record<string, number>;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  stats: GraphStats;
}

export interface GraphEntityProfile {
  personality: string;
  conv_num: number;
  conv_update_num: number;
}

/** 节点详情（图谱节点 + 实体画像联动数据） */
export interface GraphNodeDetail {
  found: boolean;
  node?: GraphNode;
  /** user/group 节点才有：主身份（别名归一后的 scope key） */
  primary?: string;
  profile?: GraphEntityProfile | null;
  aliases?: string[];
}

