import React, { memo } from 'react';
import { NodeProps, Handle, Position } from 'reactflow';
import { FileCode, Layers, Terminal } from 'lucide-react';

export const EzNode = memo(function EzNode({ id, data }: NodeProps) {
    const isFocused = Boolean(data.isFocused);
    const isCalled = Boolean(data.isCalled);
    const isCaller = Boolean(data.isCaller);
    const isDim = Boolean(data.isDim);
    const hasFocus = isFocused || isCalled || isCaller || isDim;

    let cls = 'ez-node';
    if (!hasFocus && data.isEntry) cls += ' ez-entry';
    else if (isFocused) cls += ' ez-focused';
    else if (isCalled) cls += ' ez-called';
    else if (isCaller) cls += ' ez-caller';
    else if (isDim) cls += ' ez-dim';
    else if (data.isEntry) cls += ' ez-entry';
    if (data._expandPulse) cls += ' ez-node-expand-pulse';

    const clusterColor = data.clusterColor as string | undefined;

    return (
        <div className={cls} style={clusterColor ? {
            background: `linear-gradient(145deg, ${clusterColor}, rgba(11,16,23,0.9))`,
            borderColor: clusterColor.replace('0.15', '0.3')
        } : undefined}>
            <Handle type="target" position={Position.Left} style={{ opacity: 0, width: 6, height: 6 }} />
            <Handle type="source" position={Position.Right} style={{ opacity: 0, width: 6, height: 6 }} />

            {data.hasHidden && (
                <div style={{
                    position: 'absolute', top: -6, right: -6,
                    width: 20, height: 20, borderRadius: '50%',
                    background: 'rgba(20,184,166,0.95)', border: '1px solid rgba(20,184,166,0.6)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: 11, color: '#0f172a', fontWeight: 700,
                    boxShadow: '0 2px 8px rgba(20,184,166,0.4)', zIndex: 1
                }}>
                    +
                </div>
            )}

            <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginBottom: 6 }}>
                {data.type === 'file'
                    ? <FileCode size={11} style={{ color: 'var(--t2)', flexShrink: 0 }} />
                    : data.type === 'class'
                    ? <Layers size={11} style={{ color: 'var(--am)', flexShrink: 0 }} />
                    : <Terminal size={11} style={{ color: 'var(--bl)', flexShrink: 0 }} />}
                <span style={{
                    fontSize: 8.5, fontWeight: 600, letterSpacing: '.1em', textTransform: 'uppercase',
                    color: data.type === 'class' ? 'var(--am)' : data.type === 'file' ? 'var(--t2)' : 'var(--bl)', opacity: .85
                }}>
                    {data.type}
                </span>
                {!hasFocus && (
                    data.is_root_file ? (
                        <span style={{
                            marginLeft: 'auto', fontSize: 7.5, padding: '1px 5px', borderRadius: 99,
                            background: 'rgba(20,184,166,.15)', border: '1px solid rgba(20,184,166,.3)', color: 'var(--tl)'
                        }}>
                            ROOT
                        </span>
                    ) : data.isEntry ? (
                        <span style={{
                            marginLeft: 'auto', fontSize: 7.5, padding: '1px 5px', borderRadius: 99,
                            background: 'rgba(20,184,166,.15)', border: '1px solid rgba(20,184,166,.3)', color: 'var(--tl)'
                        }}>
                            ENTRY
                        </span>
                    ) : data.is_root_dep ? (
                        <span style={{
                            marginLeft: 'auto', fontSize: 7.5, padding: '1px 5px', borderRadius: 99,
                            background: 'rgba(245,158,11,.12)', border: '1px solid rgba(245,158,11,.3)', color: 'var(--am)'
                        }}>
                            ROOT DEP
                        </span>
                    ) : null
                )}
                {isFocused && (
                    <span style={{
                        marginLeft: 'auto', fontSize: 7.5, padding: '1px 5px', borderRadius: 99,
                        background: 'rgba(59,130,246,.18)', border: '1px solid rgba(59,130,246,.4)', color: '#93c5fd'
                    }}>
                        FOCUS
                    </span>
                )}
                {isCalled && (
                    <span style={{
                        marginLeft: 'auto', fontSize: 7.5, padding: '1px 5px', borderRadius: 99,
                        background: 'rgba(20,184,166,.1)', border: '1px solid rgba(20,184,166,.25)', color: 'var(--tl)'
                    }}>
                        CALLS
                    </span>
                )}
                {isCaller && (
                    <span style={{
                        marginLeft: 'auto', fontSize: 7.5, padding: '1px 5px', borderRadius: 99,
                        background: 'rgba(245,158,11,.1)', border: '1px solid rgba(245,158,11,.25)', color: 'var(--am)'
                    }}>
                        CALLER
                    </span>
                )}
            </div>

            <div style={{
                fontWeight: 600, fontSize: 12.5,
                color: isFocused ? '#93c5fd' : 'var(--t1)',
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', marginBottom: 3
            }}>
                {data.label}
            </div>
            <div style={{ fontSize: 9, color: 'var(--t3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {data.filepath}
            </div>
        </div>
    );
});
