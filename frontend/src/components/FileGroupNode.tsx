import React, { memo } from 'react';
import { NodeProps, Handle, Position } from 'reactflow';
import { FileCode, Layers, Terminal } from 'lucide-react';
import { MemberClickCtx } from './context';

export const FileGroupNode = memo(function FileGroupNode({ data }: NodeProps) {
    const onMemberClick = React.useContext(MemberClickCtx);
    const isFocused = Boolean(data.isFocused);
    const isDim = Boolean(data.isDim);

    let cls = 'ez-fg';
    if (data.isEntry) cls += ' ez-fg-entry';
    if (isFocused) cls += ' ez-fg-focus';
    else if (isDim) cls += ' ez-fg-dim';
    if (data._expandPulse) cls += ' ez-fg-expand-pulse';

    const langColor = data.langColor as string;
    const langLabel = data.langLabel as string;
    const members = data.members as { id: string; name: string; type: string; code: string; start_line: number; end_line: number; filepath: string }[];

    return (
        <div className={cls}>
            <Handle type="target" position={Position.Left} style={{ opacity: 0, width: 6, height: 6 }} />
            <Handle type="source" position={Position.Right} style={{ opacity: 0, width: 6, height: 6 }} />

            <div className="ez-fg-hdr">
                <FileCode size={12} style={{ color: langColor, flexShrink: 0 }} />
                <span className="ez-fg-name">{data.label as string}</span>
                <span className="ez-fg-lang" style={{ color: langColor, borderColor: langColor + '55', background: langColor.startsWith('var') ? 'rgba(0,0,0,.2)' : langColor + '1a' }}>
                    {langLabel}
                </span>
                {data.isEntry && (
                    <span style={{ fontSize: 7.5, padding: '1px 5px', borderRadius: 99, background: 'rgba(20,184,166,.15)', border: '1px solid rgba(20,184,166,.3)', color: 'var(--tl)', flexShrink: 0 }}>ENTRY</span>
                )}
            </div>

            <div className="ez-fg-members">
                {members.map(m => (
                    <div key={m.id} className="ez-fmember"
                        onClick={e => { e.stopPropagation(); onMemberClick(m); }}>
                        {m.type === 'class'
                            ? <Layers size={9} style={{ color: 'var(--am)', flexShrink: 0 }} />
                            : <Terminal size={9} style={{ color: 'var(--bl)', flexShrink: 0 }} />}
                        <span className="ez-fmname">{m.name}</span>
                        <span className="ez-fmtype">{m.type}</span>
                    </div>
                ))}
            </div>
        </div>
    );
});
