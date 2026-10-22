import { useMemo, useEffect, useRef, useState, useCallback } from 'react';
import {
  ReactFlow,
  Background,
  Controls as FlowControls,
  Position,
  useReactFlow,
  ReactFlowProvider,
  getNodesBounds,
  getViewportForBounds,
  type Node as RFNode,
  type Edge as RFEdge,
} from '@xyflow/react';
import { toPng, toSvg } from 'html-to-image';
import Dagre from '@dagrejs/dagre';
import '@xyflow/react/dist/style.css';
import type { IVRNode, IVREdge } from '../types';
import { IVRNodeComponent, type IVRNodeData } from './IVRNode';

interface TreeViewProps {
  nodes: IVRNode[];
  edges: IVREdge[];
  sessionId?: string | null;
  phoneNumber?: string | null;
  onNodeClick?: (nodeId: string) => void;
}

const NODE_WIDTH = 240;
const NODE_HEIGHT = 80;

const nodeTypes = { ivr: IVRNodeComponent };

function getLayoutedElements(nodes: RFNode[], edges: RFEdge[]) {
  const g = new Dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: 'TB', nodesep: 80, ranksep: 120 });

  nodes.forEach((node) => g.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT }));
  edges.forEach((edge) => g.setEdge(edge.source, edge.target));

  Dagre.layout(g);

  const layoutedNodes = nodes.map((node) => {
    const pos = g.node(node.id);
    return {
      ...node,
      position: { x: pos.x - NODE_WIDTH / 2, y: pos.y - NODE_HEIGHT / 2 },
    };
  });

  return { nodes: layoutedNodes, edges };
}

function nodeLabel(node: IVRNode): string {
  if (node.status === 'calling') return 'Calling...';
  if (node.status === 'parsing') return 'Parsing transcript...';
  if (node.prompt_text) {
    const text = node.prompt_text.substring(0, 100);
    return text + (node.prompt_text.length > 100 ? '...' : '');
  }
  if (!node.parent_id) return 'Root';
  return node.dtmf_path || 'Pending';
}

// Filename-safe timestamp, e.g. 20261021-1530
function fileTimestamp(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(
    d.getMinutes()
  )}`;
}

function triggerDownload(href: string, filename: string) {
  const link = document.createElement('a');
  link.href = href;
  link.download = filename;
  link.click();
}

function TreeViewInner({ nodes, edges, sessionId, phoneNumber, onNodeClick }: TreeViewProps) {
  const { fitView, getNodes } = useReactFlow();
  const wrapperRef = useRef<HTMLDivElement>(null);
  const fitViewTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const [exporting, setExporting] = useState<'png' | 'svg' | 'session' | null>(null);

  // Build a map of node_id -> edge label for display on child nodes
  const edgeLabelMap = useMemo(() => {
    const map: Record<string, string> = {};
    for (const edge of edges) {
      if (edge.to_node_id) {
        map[edge.to_node_id] = edge.label || `Key ${edge.dtmf_key}`;
      }
    }
    return map;
  }, [edges]);

  const rfNodes: RFNode[] = useMemo(() => {
    return nodes.map((node) => {
      const data: IVRNodeData = {
        label: nodeLabel(node),
        edgeLabel: edgeLabelMap[node.id] || '',
        status: node.status,
        cost: node.cost,
        isRoot: !node.parent_id,
      };
      return {
        id: node.id,
        type: 'ivr',
        position: { x: 0, y: 0 },
        data,
        sourcePosition: Position.Bottom,
        targetPosition: Position.Top,
        width: NODE_WIDTH,
        height: NODE_HEIGHT,
        style: { width: NODE_WIDTH },
      };
    });
  }, [nodes, edgeLabelMap]);

  const rfEdges: RFEdge[] = useMemo(() => {
    return edges
      .filter((e) => e.to_node_id)
      .map((edge) => {
        const targetNode = nodes.find((n) => n.id === edge.to_node_id);
        const isActive =
          targetNode?.status === 'calling' || targetNode?.status === 'parsing';

        return {
          id: edge.id,
          source: edge.from_node_id,
          target: edge.to_node_id!,
          type: 'default',
          label: `${edge.dtmf_key}`,
          style: {
            stroke: isActive ? '#F59E0B' : '#6B7280',
            strokeWidth: 2,
          },
          labelStyle: {
            fill: isActive ? '#FCD34D' : '#9CA3AF',
            fontSize: '11px',
            fontWeight: 600,
          },
          labelBgStyle: {
            fill: '#030712',
            fillOpacity: 0.95,
          },
          labelBgPadding: [6, 3] as [number, number],
          labelBgBorderRadius: 6,
          animated: isActive,
        };
      });
  }, [edges, nodes]);

  const layouted = useMemo(
    () => getLayoutedElements(rfNodes, rfEdges),
    [rfNodes, rfEdges]
  );

  // Debounced fitView — triggers whenever tree structure changes.
  // Uses debounce so rapid updates (multiple nodes/edges arriving)
  // only cause one fitView after they settle.
  const structureKey = `${nodes.length}-${edges.length}`;
  useEffect(() => {
    clearTimeout(fitViewTimer.current);
    fitViewTimer.current = setTimeout(() => {
      fitView({ padding: 0.3, duration: 300 });
    }, 200);
    return () => clearTimeout(fitViewTimer.current);
  }, [structureKey, fitView]);

  const exportImage = useCallback(
    async (format: 'png' | 'svg') => {
      const flowEl = wrapperRef.current?.querySelector('.react-flow') as
        | HTMLElement
        | null;
      if (!flowEl || getNodes().length === 0) return;
      setExporting(format);
      try {
        // Fit every node (plus margin) into the export regardless of current zoom/pan.
        const nodesBounds = getNodesBounds(getNodes());
        const width = Math.max(1, Math.ceil(nodesBounds.width) + 160);
        const height = Math.max(1, Math.ceil(nodesBounds.height) + 200);
        const viewport = getViewportForBounds(
          nodesBounds,
          width,
          height,
          0.1,
          2.5,
          0.08
        );
        const captureOptions: NonNullable<Parameters<typeof toSvg>[1]> = {
          backgroundColor: '#030712',
          width,
          height,
          style: {
            width: `${width}px`,
            height: `${height}px`,
            transform: `translate(${viewport.x}px, ${viewport.y}px) scale(${viewport.zoom})`,
          },
          // Keep React Flow's zoom controls out of the exported image.
          filter: (node) =>
            !(
              node instanceof HTMLElement && node.matches('.react-flow__controls')
            ),
        };

        const base = phoneNumber ? `${phoneNumber}-` : '';
        if (format === 'svg') {
          // Vector output: stays crisp at any zoom, good for PDF/slides embeds.
          const dataUrl = await toSvg(flowEl, captureOptions);
          triggerDownload(dataUrl, `ivr-tree-${base}${fileTimestamp()}.svg`);
        } else {
          // 3x raster keeps text sharp on high-DPI screens and in print.
          const dataUrl = await toPng(flowEl, {
            ...captureOptions,
            type: 'image/png' as const,
            pixelRatio: 3,
          });
          triggerDownload(dataUrl, `ivr-tree-${base}${fileTimestamp()}.png`);
        }
      } catch (err) {
        console.error(`${format.toUpperCase()} export failed:`, err);
      } finally {
        setExporting(null);
      }
    },
    [getNodes, phoneNumber]
  );

  // Download the full shareable session bundle (tree + cached report) from the
  // backend; recipients import it with backend/scripts/import_session.py.
  const exportSession = useCallback(async () => {
    if (!sessionId) return;
    setExporting('session');
    try {
      const response = await fetch(`/api/sessions/${sessionId}/export`);
      if (!response.ok) throw new Error(`export failed: HTTP ${response.status}`);
      const blob = await response.blob();
      const disposition = response.headers.get('Content-Disposition') ?? '';
      const match = disposition.match(/filename="(.+?)"/);
      const filename = match?.[1] ?? `ivr-session-${sessionId.slice(0, 8)}.json`;
      const url = URL.createObjectURL(blob);
      triggerDownload(url, filename);
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error('Session export failed:', err);
    } finally {
      setExporting(null);
    }
  }, [sessionId]);

  const exportDisabled = exporting !== null || nodes.length === 0;

  return (
    <div ref={wrapperRef} className="relative w-full h-full">
      {/* Export toolbar */}
      <div className="absolute top-3 right-3 z-50 flex gap-1.5">
        <button
          onClick={() => void exportImage('png')}
          disabled={exportDisabled}
          title="Export the full IVR tree as a high-resolution PNG (3x)"
          className="px-2.5 py-1.5 text-xs font-medium rounded-lg border border-gray-700 bg-[#111827]/90 text-gray-300 hover:text-white hover:border-gray-500 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {exporting === 'png' ? 'Exporting…' : 'Export PNG'}
        </button>
        <button
          onClick={() => void exportImage('svg')}
          disabled={exportDisabled}
          title="Export the full IVR tree as a vector SVG — crisp at any zoom"
          className="px-2.5 py-1.5 text-xs font-medium rounded-lg border border-gray-700 bg-[#111827]/90 text-gray-300 hover:text-white hover:border-gray-500 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {exporting === 'svg' ? 'Exporting…' : 'Export SVG'}
        </button>
        <button
          onClick={() => void exportSession()}
          disabled={exportDisabled || !sessionId}
          title="Download a session bundle (tree + report) to share with another local instance"
          className="px-2.5 py-1.5 text-xs font-medium rounded-lg border border-gray-700 bg-[#111827]/90 text-gray-300 hover:text-white hover:border-gray-500 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {exporting === 'session' ? 'Exporting…' : 'Share Session'}
        </button>
      </div>
    <ReactFlow
      nodes={layouted.nodes}
      edges={layouted.edges}
      nodeTypes={nodeTypes}
      onNodeClick={(_event, node) => onNodeClick?.(node.id)}
      fitView
      fitViewOptions={{ padding: 0.3 }}
      proOptions={{ hideAttribution: true }}
      minZoom={0.1}
      maxZoom={2.5}
    >
      <Background color="#1a1a2e" gap={32} size={1} />
      <FlowControls
        showInteractive={false}
        style={{
          background: '#111827',
          borderColor: '#1F2937',
          borderRadius: '10px',
          boxShadow: '0 4px 20px rgba(0,0,0,0.3)',
        }}
      />
    </ReactFlow>
    </div>
  );
}

export function TreeView(props: TreeViewProps) {
  return (
    <ReactFlowProvider>
      <TreeViewInner {...props} />
    </ReactFlowProvider>
  );
}
