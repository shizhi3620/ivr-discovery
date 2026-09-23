import { useState, useEffect, useCallback, useRef } from 'react';
import { useWebSocket } from './hooks/useWebSocket';
import { Controls } from './components/Controls';
import { ReportView } from './components/ReportView';
import { TreeView } from './components/TreeView';
import { NodeDetail } from './components/NodeDetail';
import type {
  IVRNode,
  IVREdge,
  OptimizationReport,
  SessionInfo,
  ServerMessage,
} from './types';

// Stable WS session ID that survives HMR reloads
const WS_ID = sessionStorage.getItem('ws_session_id') ?? crypto.randomUUID();
sessionStorage.setItem('ws_session_id', WS_ID);

function getSessionIdFromUrl(): string | null {
  const path = window.location.pathname.replace(/^\//, '');
  // Must look like a UUID
  return /^[0-9a-f-]{36}$/i.test(path) ? path : null;
}

function StatusBar({ session }: { session: SessionInfo | null }) {
  if (!session || session.status === 'pending') return null;

  const progress =
    session.total_nodes > 0
      ? Math.round(
          ((session.completed_nodes + session.failed_nodes) / session.total_nodes) * 100
        )
      : 0;

  const isRunning = session.status === 'running';

  return (
    <div className="flex items-center gap-4 text-xs">
      {isRunning && (
        <div className="flex items-center gap-2">
          <div className="w-24 h-1.5 bg-gray-800 rounded-full overflow-hidden">
            <div
              className="h-full bg-indigo-500 rounded-full transition-all duration-500"
              style={{ width: `${progress}%` }}
            />
          </div>
          <span className="text-gray-500 tabular-nums">
            {session.completed_nodes}/{session.total_nodes}
          </span>
        </div>
      )}

      <span
        className={
          isRunning
            ? 'text-indigo-400'
            : session.status === 'completed'
            ? 'text-emerald-400'
            : 'text-red-400'
        }
      >
        {isRunning
          ? 'Discovering'
          : session.status === 'completed'
          ? `Done — ${session.total_nodes} nodes`
          : 'Failed'}
      </span>

      {session.failed_nodes > 0 && (
        <span className="text-red-500/70 tabular-nums">
          {session.failed_nodes} failed
        </span>
      )}

      {session.budget && (
        <span className="text-gray-500 tabular-nums">
          Budget {session.budget.window_used}/{session.budget.window_limit} window
          {' · '}
          {session.budget.target_used}/{session.budget.target_limit} total
        </span>
      )}

      {session.total_cost > 0 && (
        <span className="text-gray-600 tabular-nums">${session.total_cost.toFixed(4)}</span>
      )}
    </div>
  );
}

function App() {
  const { status, sendMessage, onMessage } = useWebSocket(WS_ID);

  const [nodes, setNodes] = useState<IVRNode[]>([]);
  const [edges, setEdges] = useState<IVREdge[]>([]);
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [view, setView] = useState<'tree' | 'report'>('tree');
  const [report, setReport] = useState<OptimizationReport | null>(null);
  const [reportLoading, setReportLoading] = useState(false);
  const [reportError, setReportError] = useState<string | null>(null);
  const [businessContext, setBusinessContext] = useState('');
  // Signature of the last server snapshot we rendered, so background polling
  // only touches React state when something actually changed.
  const snapshotRef = useRef<string>('');
  const pollTimer = useRef<ReturnType<typeof setInterval>>(undefined);

  // Restore a session on mount: from the URL, or the latest saved session on "/"
  useEffect(() => {
    const urlSessionId = getSessionIdFromUrl();
    const restore = (data: {
      session?: SessionInfo;
      nodes?: IVRNode[];
      edges?: IVREdge[];
    }) => {
      if (!data.session || !data.nodes || data.nodes.length === 0) return;
      setSession(data.session);
      setNodes(data.nodes);
      setEdges(data.edges || []);
      snapshotRef.current = '';
      if (data.session.phone_number === '4006668800') {
        setBusinessContext(
          '21:00 之前进入人工坐席服务，21:00 之后进入 IVR 自助服务。请分别分析两个时段的流程和优化建议。'
        );
      }
    };

    if (urlSessionId) {
      fetch(`/api/recover-stuck`)
        .then(() => fetch(`/api/sessions/${urlSessionId}`))
        .then((r) => r.json())
        .then(restore)
        .catch((e) => console.error('Failed to restore session:', e));
      return;
    }

    // No session in the URL: show the most recent saved session so a reload
    // never lands on an empty page while the backend still has data.
    fetch('/api/sessions/latest')
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!data) return;
        restore(data);
        if (data.session?.id) {
          window.history.replaceState(null, '', `/${data.session.id}`);
        }
      })
      .catch((e) => console.error('Failed to load latest session:', e));
  }, []);

  const applySnapshot = useCallback(
    (data: {
      session?: SessionInfo;
      nodes?: IVRNode[];
      edges?: IVREdge[];
    }): boolean => {
      if (!data.session || !data.nodes || data.nodes.length === 0) return false;
      const signature = JSON.stringify([
        data.session.status,
        data.session.total_nodes,
        data.session.completed_nodes,
        data.session.failed_nodes,
        data.session.budget ?? null,
        data.nodes.map((n) => [n.id, n.status, n.prompt_text, n.parent_id, n.dtmf_path]),
      ]);
      if (signature === snapshotRef.current) return false;
      snapshotRef.current = signature;
      setSession(data.session);
      setNodes(data.nodes);
      setEdges(data.edges || []);
      return true;
    },
    []
  );

  // Live refresh: the backend WebSocket only streams events to the connection
  // that started a run. Changes made by the rebuild scripts, another browser,
  // or a run started elsewhere would otherwise not appear until a manual
  // reload, so poll the saved session and apply snapshots when they change.
  useEffect(() => {
    clearInterval(pollTimer.current);
    const sessionId = session?.id;
    if (!sessionId) return;

    const poll = () => {
      fetch(`/api/sessions/${sessionId}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (data) applySnapshot(data);
        })
        .catch(() => {
          // Transient failures are fine; the next tick retries.
        });
    };
    pollTimer.current = setInterval(poll, 3000);
    return () => clearInterval(pollTimer.current);
  }, [session?.id, applySnapshot]);

  useEffect(() => {
    onMessage((msg: ServerMessage) => {
      switch (msg.type) {
        case 'node_added':
          setNodes((prev) =>
            prev.some((n) => n.id === msg.node.id) ? prev : [...prev, msg.node]
          );
          break;
        case 'node_updated':
          setNodes((prev) =>
            prev.map((n) =>
              n.id === msg.node_id
                ? {
                    ...n,
                    status: msg.status ?? n.status,
                    prompt_text: msg.prompt_text ?? n.prompt_text,
                    cost: msg.cost ?? n.cost,
                    call_id: msg.call_id ?? n.call_id,
                  }
                : n
            )
          );
          break;
        case 'edge_added':
          setEdges((prev) =>
            prev.some((e) => e.id === msg.edge.id) ? prev : [...prev, msg.edge]
          );
          break;
        case 'session_status':
          setSession(msg.session);
          // Update URL when we get the first session status (discovery started)
          if (msg.session.id && window.location.pathname === '/') {
            window.history.pushState(null, '', `/${msg.session.id}`);
          }
          break;
        case 'session_snapshot':
          applySnapshot({
            session: msg.session,
            nodes: msg.nodes,
            edges: msg.edges,
          });
          if (msg.session.id && window.location.pathname === '/') {
            window.history.pushState(null, '', `/${msg.session.id}`);
          }
          break;
        case 'live_transcript':
          setNodes((prev) =>
            prev.map((n) =>
              n.id === msg.node_id
                ? { ...n, prompt_text: msg.text.substring(msg.text.length - 120) }
                : n
            )
          );
          break;
        case 'subtree_cleared': {
          const deletedNodes = new Set(msg.deleted_node_ids);
          const deletedEdges = new Set(msg.deleted_edge_ids);
          setNodes((prev) => prev.filter((n) => !deletedNodes.has(n.id)));
          setEdges((prev) => prev.filter((e) => !deletedEdges.has(e.id)));
          break;
        }
        case 'error':
          console.error('Server error:', msg.message);
          break;
      }
    });
  }, [onMessage, applySnapshot]);

  const handleDiscover = useCallback(
    (phoneNumber: string) => {
      setNodes([]);
      setEdges([]);
      setSession(null);
      setSelectedNodeId(null);
      setView('tree');
      setReport(null);
      setReportError(null);
      if (phoneNumber === '4006668800') {
        setBusinessContext(
          '21:00 之前进入人工坐席服务，21:00 之后进入 IVR 自助服务。请分别分析两个时段的流程和优化建议。'
        );
      } else {
        setBusinessContext('');
      }
      sendMessage({ type: 'start_discovery', phone_number: phoneNumber });
    },
    [sendMessage]
  );

  const handleStop = useCallback(() => {
    sendMessage({ type: 'cancel' });
  }, [sendMessage]);

  const handleClear = useCallback(() => {
    setNodes([]);
    setEdges([]);
    setSession(null);
    setSelectedNodeId(null);
    setView('tree');
    setReport(null);
    setReportError(null);
    window.history.pushState(null, '', '/');
  }, []);

  const handleGenerateReport = useCallback(
    async (force = false) => {
      if (!session?.id) return;
      setReportLoading(true);
      setReportError(null);
      try {
        const response = await fetch(
          `/api/sessions/${session.id}/optimization-report`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              business_context: businessContext,
              force,
            }),
          }
        );
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.detail || 'Report generation failed');
        }
        setReport(data.report);
      } catch (error) {
        setReportError(
          error instanceof Error ? error.message : 'Report generation failed'
        );
      } finally {
        setReportLoading(false);
      }
    },
    [businessContext, session?.id]
  );

  const handleRediscover = useCallback(
    (nodeId: string) => {
      setSelectedNodeId(null);
      sendMessage({ type: 'rediscover_subtree', node_id: nodeId });
    },
    [sendMessage]
  );

  const isRunning = session?.status === 'running';
  const hasSession = nodes.length > 0;
  const selectedNode = selectedNodeId
    ? nodes.find((n) => n.id === selectedNodeId) ?? null
    : null;

  return (
    <div className="h-full flex flex-col bg-[#030712] text-white">
      {/* Header */}
      <header className="flex items-center justify-between px-5 py-3 border-b border-gray-800/50 bg-[#030712]/80 backdrop-blur-md z-10">
        <div className="flex items-center gap-5">
          <h1
            className="text-base font-semibold tracking-tight text-gray-200 cursor-pointer hover:text-white transition-colors"
            onClick={handleClear}
          >
            IVR Discovery
          </h1>
          <div className="w-px h-5 bg-gray-800" />
          <Controls
            onDiscover={handleDiscover}
            onStop={handleStop}
            onClear={handleClear}
            isRunning={isRunning}
            hasSession={hasSession}
          />
          {hasSession && (
            <div className="flex rounded-lg border border-gray-800 p-0.5">
              <button
                onClick={() => setView('tree')}
                className={`px-3 py-1 text-xs rounded-md transition-colors ${
                  view === 'tree'
                    ? 'bg-gray-800 text-white'
                    : 'text-gray-500 hover:text-gray-200'
                }`}
              >
                Tree
              </button>
              <button
                onClick={() => {
                  setView('report');
                  if (session?.status === 'completed' && !report && !reportLoading) {
                    void handleGenerateReport(false);
                  }
                }}
                className={`px-3 py-1 text-xs rounded-md transition-colors ${
                  view === 'report'
                    ? 'bg-gray-800 text-white'
                    : 'text-gray-500 hover:text-gray-200'
                }`}
              >
                Optimization
              </button>
            </div>
          )}
        </div>

        <div className="flex items-center gap-4">
          <StatusBar session={session} />
          <div className="w-px h-4 bg-gray-800" />
          <div className="flex items-center gap-1.5">
            <div
              className={`w-2 h-2 rounded-full transition-colors ${
                status === 'connected'
                  ? 'bg-emerald-500'
                  : status === 'connecting'
                  ? 'bg-amber-500 animate-pulse'
                  : 'bg-red-500'
              }`}
            />
            <span className="text-[11px] text-gray-600 capitalize">{status}</span>
          </div>
        </div>
      </header>

      {/* Tree + Detail Panel */}
      <main className="flex-1 flex overflow-hidden">
        <div className="flex-1 relative">
          {view === 'report' ? (
            <ReportView
              sessionId={session?.id ?? null}
              report={report}
              loading={reportLoading}
              error={reportError}
              canGenerate={session?.status === 'completed'}
              businessContext={businessContext}
              onBusinessContextChange={setBusinessContext}
              onGenerate={(force) => void handleGenerateReport(force)}
            />
          ) : hasSession ? (
            <TreeView
              nodes={nodes}
              edges={edges}
              onNodeClick={(id) => setSelectedNodeId(id === selectedNodeId ? null : id)}
            />
          ) : (
            <div className="h-full flex flex-col items-center justify-center gap-3">
              <div className="text-gray-700 text-sm">
                Enter a phone number to map its IVR tree
              </div>
            </div>
          )}
        </div>

        {view === 'tree' && selectedNode && (
          <NodeDetail
            node={selectedNode}
            edges={edges}
            allNodes={nodes}
            onClose={() => setSelectedNodeId(null)}
            onRediscover={handleRediscover}
          />
        )}
      </main>
    </div>
  );
}

export default App;
