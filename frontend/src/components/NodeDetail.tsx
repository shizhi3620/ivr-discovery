import { useEffect, useState } from 'react';
import type { IVRNode, IVREdge, NodeStatus } from '../types';

interface NodeDetailProps {
  node: IVRNode;
  edges: IVREdge[];
  allNodes: IVRNode[];
  onClose: () => void;
  onRediscover?: (nodeId: string) => void;
}

interface FullNodeData {
  transcript: string | null;
  voice_option: string;
}

const STATUS_DISPLAY: Record<NodeStatus, { label: string; color: string; dot: string }> = {
  pending: { label: 'Pending', color: 'text-gray-400', dot: 'bg-gray-500' },
  calling: { label: 'Calling', color: 'text-amber-400', dot: 'bg-amber-500' },
  parsing: { label: 'Parsing', color: 'text-blue-400', dot: 'bg-blue-500' },
  completed: { label: 'Completed', color: 'text-emerald-400', dot: 'bg-emerald-500' },
  failed: { label: 'Failed', color: 'text-red-400', dot: 'bg-red-500' },
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="text-[10px] font-semibold uppercase tracking-widest text-gray-500 mb-2">
        {title}
      </h3>
      {children}
    </div>
  );
}

/** Parse "user: ...\nassistant: ..." concatenated transcript into turns. */
function parseTranscript(raw: string): { role: 'ivr' | 'agent'; text: string }[] {
  const turns: { role: 'ivr' | 'agent'; text: string }[] = [];
  const lines = raw.split('\n');

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;

    if (trimmed.startsWith('user:')) {
      const text = trimmed.slice(5).trim();
      if (text) turns.push({ role: 'ivr', text });
    } else if (trimmed.startsWith('assistant:')) {
      const text = trimmed.slice(10).trim();
      if (text) turns.push({ role: 'agent', text });
    } else {
      // Continuation of previous turn or unknown format
      if (turns.length > 0) {
        turns[turns.length - 1].text += ' ' + trimmed;
      } else {
        turns.push({ role: 'ivr', text: trimmed });
      }
    }
  }

  return turns;
}

export function NodeDetail({ node, edges, allNodes, onClose, onRediscover }: NodeDetailProps) {
  const [fullData, setFullData] = useState<FullNodeData | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`/api/nodes/${node.id}`)
      .then((r) => r.json())
      .then((data) => {
        if (!cancelled) {
          setFullData({
            transcript: data.transcript || null,
            voice_option: data.voice_option || '',
          });
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [node.id]);

  const statusCfg = STATUS_DISPLAY[node.status];
  const childEdges = edges.filter((e) => e.from_node_id === node.id);
  const parentEdge = edges.find((e) => e.to_node_id === node.id);

  const transcriptTurns = fullData?.transcript
    ? parseTranscript(fullData.transcript)
    : [];

  return (
    <div className="w-[380px] h-full bg-[#0a0a14] border-l border-gray-800/50 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-gray-800/50 shrink-0">
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full ${statusCfg.dot}`} />
          <span className={`text-sm font-medium ${statusCfg.color}`}>
            {statusCfg.label}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {onRediscover && (node.status === 'completed' || node.status === 'failed') && (
            <button
              onClick={() => onRediscover(node.id)}
              className="text-[10px] font-semibold uppercase tracking-wider text-indigo-400 hover:text-indigo-300 bg-indigo-500/10 hover:bg-indigo-500/20 px-2.5 py-1 rounded-md transition-colors"
            >
              Re-discover
            </button>
          )}
          <button
            onClick={onClose}
            className="text-gray-600 hover:text-gray-300 transition-colors text-lg leading-none px-1"
          >
            &times;
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5">
        {/* Navigation path */}
        {parentEdge && (
          <Section title="Navigation">
            <div className="text-sm text-gray-300">
              {parentEdge.dtmf_key === 'no-input' ? (
                <span>
                  <span className="text-yellow-400">No key pressed</span>
                  <span className="text-gray-500"> — {parentEdge.label}</span>
                </span>
              ) : parentEdge.dtmf_key.startsWith('say') ? (
                <span>
                  Said <span className="text-indigo-400">"{parentEdge.label}"</span>
                </span>
              ) : (
                <span>
                  Pressed{' '}
                  <span className="font-mono text-indigo-400 bg-indigo-500/10 px-1.5 py-0.5 rounded">
                    {parentEdge.dtmf_key}
                  </span>
                  <span className="text-gray-500"> — {parentEdge.label}</span>
                </span>
              )}
            </div>
            {node.dtmf_path && (
              <div className="mt-1.5 text-xs text-gray-600 font-mono">
                Path: {node.dtmf_path}
              </div>
            )}
          </Section>
        )}

        {/* Prompt text */}
        {node.prompt_text && (
          <Section title="IVR Prompt">
            <p className="text-sm text-gray-300 leading-relaxed">{node.prompt_text}</p>
          </Section>
        )}

        {/* Discovered options */}
        {childEdges.length > 0 && (
          <Section title={`Options Found (${childEdges.length})`}>
            <div className="space-y-1">
              {childEdges.map((edge) => {
                const childNode = allNodes.find((n) => n.id === edge.to_node_id);
                const childStatus = childNode ? STATUS_DISPLAY[childNode.status] : null;
                return (
                  <div
                    key={edge.id}
                    className="flex items-center gap-2.5 py-1.5 px-2.5 rounded-lg bg-gray-900/50"
                  >
                    <span className="text-xs font-mono text-indigo-400 bg-indigo-500/10 px-1.5 py-0.5 rounded w-6 text-center shrink-0">
                      {edge.dtmf_key === 'no-input'
                        ? '⏱'
                        : edge.dtmf_key.startsWith('say')
                        ? 'V'
                        : edge.dtmf_key}
                    </span>
                    <span className="text-sm text-gray-300 flex-1 truncate">
                      {edge.label}
                    </span>
                    {childStatus && (
                      <div
                        className={`w-1.5 h-1.5 rounded-full ${childStatus.dot} shrink-0`}
                      />
                    )}
                  </div>
                );
              })}
            </div>
          </Section>
        )}

        {/* Call details */}
        {(node.cost > 0 || node.call_id) && (
          <Section title="Call Info">
            <div className="space-y-1.5 text-sm">
              {node.cost > 0 && (
                <div className="flex justify-between">
                  <span className="text-gray-500">Cost</span>
                  <span className="text-gray-300 tabular-nums">
                    ${node.cost.toFixed(4)}
                  </span>
                </div>
              )}
              {node.call_id && (
                <div className="flex justify-between">
                  <span className="text-gray-500">Call ID</span>
                  <span className="text-gray-500 font-mono text-xs truncate ml-4">
                    {node.call_id}
                  </span>
                </div>
              )}
            </div>
          </Section>
        )}

        {/* Transcript */}
        {transcriptTurns.length > 0 && (
          <Section title="Call Transcript">
            <div className="space-y-2.5">
              {transcriptTurns.map((turn, i) => (
                <div key={i} className="flex gap-2">
                  <span
                    className={`text-[10px] font-bold uppercase tracking-wider mt-0.5 shrink-0 w-9 ${
                      turn.role === 'ivr' ? 'text-emerald-600' : 'text-blue-600'
                    }`}
                  >
                    {turn.role === 'ivr' ? 'IVR' : 'Agent'}
                  </span>
                  <p className="text-sm text-gray-400 leading-relaxed flex-1">
                    {turn.text}
                  </p>
                </div>
              ))}
            </div>
          </Section>
        )}
      </div>
    </div>
  );
}
