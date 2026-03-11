import { memo, useState } from 'react';
import { ChevronDown, ChevronRight, Folder, FileCode } from 'lucide-react';

export const FileItem = memo(function FileItem({ name, type, depth, children }: any) {
    const [open, setOpen] = useState(false);
    return (
        <div>
            <div
                onClick={() => setOpen(o => !o)}
                style={{
                    display: 'flex', alignItems: 'center', gap: 5,
                    padding: `3px 8px 3px ${depth * 14 + 6}px`,
                    cursor: 'pointer', borderRadius: 4, fontSize: 11, color: 'var(--t2)',
                    transition: 'background .1s, color .1s',
                }}
                onMouseEnter={e => { e.currentTarget.style.background = 'rgba(59,130,246,.08)'; e.currentTarget.style.color = 'var(--t1)'; }}
                onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--t2)'; }}
            >
                {type === 'folder'
                    ? (open ? <ChevronDown size={10} /> : <ChevronRight size={10} />)
                    : <span style={{ width: 10 }} />}
                {type === 'folder'
                    ? <Folder   size={11} style={{ color: 'var(--bl)', opacity: .65 }} />
                    : <FileCode size={11} style={{ color: 'var(--t3)' }} />}
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{name}</span>
            </div>
            {open && children?.map((c: any) => <FileItem key={c.path} {...c} depth={depth + 1} />)}
        </div>
    );
});