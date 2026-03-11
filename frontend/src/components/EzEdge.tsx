import { memo } from 'react';
import { EdgeProps } from 'reactflow';

export const EzEdge = memo(function EzEdge({
    sourceX, sourceY, targetX, targetY, data,
}: EdgeProps) {
    const { line, inTree } = (data ?? {}) as { line?: number; inTree?: boolean };

    const midX = sourceX + (targetX - sourceX) * 0.45;
    const pathD = `M ${sourceX},${sourceY} C ${midX},${sourceY} ${midX},${targetY} ${targetX},${targetY}`;

    const color = (data as any)?._color as string ?? (inTree ? 'var(--c4)' : 'var(--c3)');
    const sw    = (data as any)?._sw as number ?? 1;
    const so    = (data as any)?._so as number ?? (inTree ? 0.5 : 0.18);
    const isActive    = (data as any)?._active as boolean;
    const expandPulse = (data as any)?._expandPulse as boolean;

    const labelX = midX + 6;
    const labelY = (sourceY + targetY) / 2;
    const showLabel = inTree && (line ?? 0) > 0 && Math.abs(targetY - sourceY) > 24;

    return (
        <g className={`ez-edge-g ${expandPulse ? 'ez-edge-expand-pulse' : ''}`} style={{ vectorEffect: 'non-scaling-stroke' }}>
            {!isActive && (
                <path d={pathD} fill="none" stroke={color} strokeWidth={sw} strokeOpacity={so} strokeLinecap="round" />
            )}
            {isActive && (
                <>
                    <path d={pathD} fill="none" stroke={color} strokeWidth={sw + 0.5} strokeOpacity={so} strokeLinecap="round" />
                    <path d={pathD} fill="none" stroke={color} strokeWidth={sw + 0.5} strokeOpacity={0.4}
                        strokeDasharray="6 8" strokeLinecap="round" className="ez-flow" />
                </>
            )}
            {expandPulse && (
                <path className="ez-edge-pulse" d={pathD} fill="none" stroke="#a78bfa" strokeWidth={3} strokeOpacity={0.95}
                    strokeLinecap="round" pathLength={1} strokeDasharray="0.07 1"
                    style={{ animation: 'ez-edge-pulse-draw 1.2s ease-out forwards' }} />
            )}
            <path
                d={`M ${targetX - 6},${targetY - 5} L ${targetX},${targetY} L ${targetX - 6},${targetY + 5}`}
                fill="none" stroke={color} strokeWidth={sw} strokeOpacity={so}
                strokeLinejoin="round" strokeLinecap="round"
            />
            {showLabel && (
                <text x={labelX} y={labelY} fontSize={8} fill="var(--t3)" fontFamily="var(--mono)" dominantBaseline="middle"
                    style={{ userSelect: 'none', pointerEvents: 'none' }}>
                    L{line}
                </text>
            )}
        </g>
    );
});