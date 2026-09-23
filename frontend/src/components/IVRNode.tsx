import { memo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import type { NodeStatus } from '../types';

export interface IVRNodeData {
  label: string;
  edgeLabel: string;
  status: NodeStatus;
  cost: number;
  isRoot: boolean;
  [key: string]: unknown;
}

const STATUS_CONFIG: Record<
  NodeStatus,
  { bg: string; border: string; glow: string; icon: string; accent: string }
> = {
  pending: {
    bg: 'bg-gray-800/80',
    border: 'border-gray-600/50',
    glow: '',
    icon: '○',
    accent: 'text-gray-400',
  },
  calling: {
    bg: 'bg-amber-950/80',
    border: 'border-amber-500/70',
    glow: 'shadow-[0_0_20px_rgba(245,158,11,0.15)]',
    icon: '◉',
    accent: 'text-amber-400',
  },
  parsing: {
    bg: 'bg-blue-950/80',
    border: 'border-blue-500/70',
    glow: 'shadow-[0_0_20px_rgba(59,130,246,0.15)]',
    icon: '◎',
    accent: 'text-blue-400',
  },
  completed: {
    bg: 'bg-emerald-950/60',
    border: 'border-emerald-500/50',
    glow: '',
    icon: '●',
    accent: 'text-emerald-400',
  },
  failed: {
    bg: 'bg-red-950/60',
    border: 'border-red-500/50',
    glow: '',
    icon: '✕',
    accent: 'text-red-400',
  },
};

function statusLabel(status: NodeStatus): string {
  switch (status) {
    case 'calling':
      return 'Calling...';
    case 'parsing':
      return 'Parsing...';
    case 'failed':
      return 'Failed';
    default:
      return '';
  }
}

export const IVRNodeComponent = memo(({ data }: NodeProps) => {
  const d = data as unknown as IVRNodeData;
  const isHuman = d.label.startsWith('[Human]');
  const isCycle = d.label.startsWith('(cycle)');
  const isTimeout = d.label.startsWith('(timeout)');
  const cfg = isHuman
    ? {
        bg: 'bg-violet-950/70',
        border: 'border-violet-500/60',
        glow: 'shadow-[0_0_20px_rgba(139,92,246,0.15)]',
        icon: '☎',
        accent: 'text-violet-400',
      }
    : isCycle
    ? {
        bg: 'bg-orange-950/60',
        border: 'border-orange-500/40',
        glow: '',
        icon: '↻',
        accent: 'text-orange-400',
      }
    : isTimeout
    ? {
        bg: 'bg-yellow-950/60',
        border: 'border-yellow-600/50',
        glow: '',
        icon: '⏱',
        accent: 'text-yellow-400',
      }
    : STATUS_CONFIG[d.status];
  const showStatusBadge = d.status === 'calling' || d.status === 'parsing';
  const isPulsing = d.status === 'calling';

  // Title: for completed nodes show prompt_text, otherwise show the edge label
  const displayLabel = isHuman
    ? d.label.replace('[Human] ', '')
    : isCycle
    ? d.label.replace('(cycle) ', '')
    : isTimeout
    ? d.label.replace('(timeout) ', '')
    : d.label;
  const title =
    d.status === 'completed' || d.status === 'failed'
      ? displayLabel
      : d.edgeLabel || d.label;

  return (
    <div
      className={`
        relative px-4 py-3 rounded-xl border backdrop-blur-sm
        ${cfg.bg} ${cfg.border} ${cfg.glow}
        transition-all duration-300 ease-out
        w-[240px]
      `}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="!bg-gray-500 !border-gray-400 !w-2 !h-2"
      />

      <div className="flex items-start gap-2.5">
        {/* Status dot */}
        <span
          className={`
            text-[10px] mt-0.5 shrink-0 ${cfg.accent}
            ${isPulsing ? 'animate-pulse' : ''}
          `}
        >
          {cfg.icon}
        </span>

        <div className="flex-1 min-w-0">
          {/* Edge label tag for non-root nodes */}
          {!d.isRoot && d.edgeLabel && d.status !== 'pending' && (
            <div className="text-[10px] text-gray-500 font-medium uppercase tracking-wider mb-0.5 truncate">
              {d.edgeLabel}
            </div>
          )}

          {/* Main label */}
          <div
            className={`text-[13px] leading-snug font-medium text-gray-100 ${
              d.status === 'pending' ? 'text-gray-300' : ''
            }`}
            style={{
              display: '-webkit-box',
              WebkitLineClamp: d.isRoot ? 3 : 2,
              WebkitBoxOrient: 'vertical',
              overflow: 'hidden',
            }}
          >
            {title}
          </div>

          {/* Status badge */}
          {showStatusBadge && (
            <div
              className={`mt-1.5 text-[10px] font-semibold tracking-wide uppercase ${cfg.accent}`}
            >
              {statusLabel(d.status)}
            </div>
          )}

          {/* Human transfer badge */}
          {isHuman && (
            <div className="mt-1.5 text-[10px] font-semibold tracking-wide uppercase text-violet-400">
              Human Transfer
            </div>
          )}

          {/* Cycle badge */}
          {isCycle && (
            <div className="mt-1.5 text-[10px] font-semibold tracking-wide uppercase text-orange-400">
              Cycle Detected
            </div>
          )}

          {/* No-input timeout badge */}
          {isTimeout && (
            <div className="mt-1.5 text-[10px] font-semibold tracking-wide uppercase text-yellow-400">
              No-input Timeout
            </div>
          )}

          {/* Cost for completed nodes */}
          {d.status === 'completed' && d.cost > 0 && (
            <div className="mt-1 text-[10px] text-gray-500">
              ${d.cost.toFixed(4)}
            </div>
          )}
        </div>
      </div>

      <Handle
        type="source"
        position={Position.Bottom}
        className="!bg-gray-500 !border-gray-400 !w-2 !h-2"
      />
    </div>
  );
});

IVRNodeComponent.displayName = 'IVRNodeComponent';
