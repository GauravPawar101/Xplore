import { memo } from 'react';
import { NodeProps, Handle, Position } from 'reactflow';
import { Package } from 'lucide-react';

/**
 * LibraryNode — represents a third-party package dependency.
 */
export const LibraryNode = memo(function LibraryNode({ data }: NodeProps) {
    const isDim     = Boolean(data.isDim);
    const isCalled  = Boolean(data.isCalled);
    const isCaller  = Boolean(data.isCaller);
    const isFocused = Boolean(data.isFocused);

    const active  = isFocused || isCalled || isCaller;
    const opacity = isDim ? 0.2 : 1;

    const borderColor = active ? 'rgba(139,92,246,0.85)' : 'rgba(99,102,241,0.35)';
    const bg = active
        ? 'linear-gradient(145deg, rgba(139,92,246,0.35), rgba(99,102,241,0.2))'
        : isDim
            ? 'rgba(15,23,42,0.6)'
            : 'linear-gradient(145deg, rgba(99,102,241,0.18), rgba(139,92,246,0.10))';
    const iconColor = active ? 'rgba(196,181,253,1)' : isDim ? 'rgba(99,102,241,0.25)' : 'rgba(139,92,246,0.75)';
    const textColor = active ? 'rgba(221,214,254,1)' : isDim ? 'rgba(148,163,184,0.2)' : 'rgba(196,181,253,0.85)';

    return (
        <div
            title={`Third-party package: ${data.label}`}
            style={{
                width: 68, height: 68, borderRadius: '50%',
                background: bg, border: `1.5px solid ${borderColor}`,
                display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 3,
                opacity, transition: 'opacity 0.2s ease, border-color 0.2s ease, background 0.2s ease',
                cursor: 'default',
                boxShadow: active ? '0 0 16px rgba(139,92,246,0.35)' : isDim ? 'none' : '0 0 8px rgba(99,102,241,0.15)',
                userSelect: 'none', pointerEvents: 'all',
            }}
        >
            <Handle type="target" position={Position.Left}  style={{ opacity: 0, width: 6, height: 6 }} />
            <Handle type="source" position={Position.Right} style={{ opacity: 0, width: 6, height: 6 }} />

            <Package size={13} style={{ color: iconColor, flexShrink: 0 }} />

            <div style={{
                fontSize: 8.5, fontWeight: 700, color: textColor,
                textAlign: 'center', maxWidth: 56,
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                letterSpacing: '0.03em', lineHeight: 1.2, padding: '0 4px',
            }}>
                {data.label}
            </div>

            <div style={{
                fontSize: 6.5, fontWeight: 600,
                color: isDim ? 'rgba(99,102,241,0.2)' : 'rgba(139,92,246,0.6)',
                letterSpacing: '0.08em', textTransform: 'uppercase',
            }}>
                lib
            </div>
        </div>
    );
});