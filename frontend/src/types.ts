export type NodeStatus = 'pending' | 'calling' | 'parsing' | 'completed' | 'failed';
export type SessionStatus = 'pending' | 'running' | 'completed' | 'failed';

export interface IVRNode {
  id: string;
  session_id: string;
  parent_id: string | null;
  dtmf_path: string;
  prompt_text: string;
  status: NodeStatus;
  call_id: string | null;
  cost: number;
  created_at: string;
}

export interface IVREdge {
  id: string;
  from_node_id: string;
  to_node_id: string | null;
  dtmf_key: string;
  label: string;
}

export interface SessionInfo {
  id: string;
  target_id?: string | null;
  discovery_window_id?: string | null;
  phone_number: string;
  status: SessionStatus;
  total_cost: number;
  total_nodes: number;
  completed_nodes: number;
  failed_nodes: number;
  planned_route?: 'human' | 'self-service' | null;
  budget?: {
    target_limit: number;
    target_used: number;
    target_remaining: number;
    window_limit: number;
    window_used: number;
    window_remaining: number;
  };
}

export interface ReportTimeRoute {
  window?: string;
  behavior?: string;
  evidence?: string;
  confidence?: string;
  recommendation?: string;
}

export interface ReportFlowNode {
  node_id?: string;
  path?: string;
  prompt?: string;
  options?: Array<{ key?: string; label?: string }>;
  observation?: string;
}

export interface ReportIssue {
  severity?: string;
  node_id?: string;
  finding?: string;
  recommendation?: string;
  expected_impact?: string;
}

export interface LocalizedOptimizationReport {
  title: string;
  executive_summary: string;
  time_routing: ReportTimeRoute[];
  current_flow: ReportFlowNode[];
  issues: ReportIssue[];
  proposed_flow: string;
  metrics: string[];
  validation_plan: string[];
  unknown_items: string[];
}

export interface OptimizationReport {
  zh: LocalizedOptimizationReport;
  en: LocalizedOptimizationReport;
  generated_at?: string;
  phone_number?: string;
  business_context?: string;
  target_id?: string;
  draft?: boolean;
  missing_routes?: string[];
}

// WebSocket message types
export type ServerMessage =
  | { type: 'connected'; session_id: string }
  | { type: 'node_added'; node: IVRNode }
  | { type: 'node_updated'; node_id: string; status: NodeStatus; prompt_text?: string; cost?: number; call_id?: string }
  | { type: 'edge_added'; edge: IVREdge }
  | { type: 'session_status'; session: SessionInfo }
  | { type: 'live_transcript'; node_id: string; text: string }
  | { type: 'subtree_cleared'; node_id: string; deleted_node_ids: string[]; deleted_edge_ids: string[] }
  | { type: 'error'; message: string };

export type ClientMessage =
  | { type: 'start_discovery'; phone_number: string }
  | { type: 'rediscover_subtree'; node_id: string }
  | { type: 'cancel' };
