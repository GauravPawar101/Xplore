import { Node, Edge } from 'reactflow';
import { RN, RE } from '@/types';
import {
    LEVEL_W,
    LINE_SCALE,
    NODE_W,
    NODE_H,
    NODE_GAP,
    ENTRY_KW,
    FILE_COL_W,
    FILE_ROW_GAP,
    MEMBER_H,
    HEADER_H,
    FILE_NODE_W,
} from '@/config/constants';

// ─── Cluster color by depth ───────────────────────────────────────────────────
const BASE_COLORS = [
    '59,130,246',   // blue
    '20,184,166',   // teal
    '245,158,11',   // amber
    '244,63,94',    // rose
    '168,85,247',   // purple
    '34,197,94',    // green
];

export function getClusterColor(filepath: string, depth = 0): string {
    const hash = filepath.split('').reduce((acc, c) => acc + c.charCodeAt(0), 0);
    const rgb = BASE_COLORS[hash % BASE_COLORS.length];
    const opacity = Math.max(0.04, 0.22 / Math.pow(1.8, depth));
    return `rgba(${rgb},${opacity.toFixed(3)})`;
}

export function langInfo(filename: string): { label: string; color: string } {
    const ext = filename.split('.').pop()?.toLowerCase() ?? '';
    if (ext === 'py') return { label: 'Python', color: 'var(--gr)' };
    if (ext === 'ts' || ext === 'tsx') return { label: 'TypeScript', color: 'var(--bl)' };
    if (ext === 'js' || ext === 'jsx') return { label: 'JavaScript', color: '#f0db4f' };
    if (ext === 'java') return { label: 'Java', color: 'var(--am)' };
    if (ext === 'rs') return { label: 'Rust', color: '#f74c00' };
    return { label: ext.toUpperCase() || '?', color: 'var(--t3)' };
}

// ─── treeLayout ───────────────────────────────────────────────────────────────

export function treeLayout(
    rawNodes: RN[],
    rawEdges: RE[]
): { nodes: Node[]; edges: Edge[]; clusters: any[] } {
    if (!rawNodes.length) return { nodes: [], edges: [], clusters: [] };

    const fileGroups = new Map<string, RN[]>();
    for (const n of rawNodes) {
        const fp = (n.data.filepath as string) ?? 'unknown';
        if (!fileGroups.has(fp)) fileGroups.set(fp, []);
        fileGroups.get(fp)!.push(n);
    }

    const nodeToFile = new Map<string, string>();
    for (const n of rawNodes) nodeToFile.set(n.id, (n.data.filepath as string) ?? 'unknown');

    const adj = new Map<string, { target: string; line: number }[]>();
    for (const e of rawEdges) {
        const line = parseInt(String(e.label ?? e.data?.line_number ?? '0'), 10) || 0;
        if (!adj.has(e.source)) adj.set(e.source, []);
        adj.get(e.source)!.push({ target: e.target, line });
    }

    const fileAdj = new Map<string, Set<string>>();
    for (const e of rawEdges) {
        const sf = nodeToFile.get(e.source);
        const tf = nodeToFile.get(e.target);
        if (sf && tf && sf !== tf) {
            if (!fileAdj.has(sf)) fileAdj.set(sf, new Set());
            fileAdj.get(sf)!.add(tf);
        }
    }

    const callLine = new Map<string, number>();
    for (const [, targets] of adj) {
        for (const { target, line } of targets) {
            if (line > 0 && (!callLine.has(target) || line < callLine.get(target)!))
                callLine.set(target, line);
        }
    }

    const entry =
        rawNodes.find((n) => {
            const lb = (n.data.label ?? '').toLowerCase();
            const fp = (n.data.filepath ?? '').toLowerCase();
            return (
                ENTRY_KW.has(lb) ||
                [...ENTRY_KW].some((k) =>
                    fp.match(new RegExp(`[/\\\\]${k}\\.(py|js|ts|tsx|java|rs)$`))
                )
            );
        }) ?? rawNodes[0];

    const entryFile = nodeToFile.get(entry.id) ?? 'unknown';

    const fileDepth = new Map<string, number>([[entryFile, 0]]);
    const bfsQ = [entryFile];
    const bfsSeen = new Set([entryFile]);
    while (bfsQ.length) {
        const cur = bfsQ.shift()!;
        for (const next of fileAdj.get(cur) ?? new Set()) {
            if (!bfsSeen.has(next)) {
                bfsSeen.add(next);
                fileDepth.set(next, fileDepth.get(cur)! + 1);
                bfsQ.push(next);
            }
        }
    }
    const maxD = Math.max(0, ...fileDepth.values());
    for (const fp of fileGroups.keys())
        if (!fileDepth.has(fp)) fileDepth.set(fp, maxD + 1);

    const nodeDepth = new Map<string, number>([[entry.id, 0]]);
    const nodeBfsQ = [entry.id];
    const nodeBfsSeen = new Set([entry.id]);
    while (nodeBfsQ.length) {
        const cur = nodeBfsQ.shift()!;
        for (const { target } of adj.get(cur) ?? []) {
            if (!nodeBfsSeen.has(target)) {
                nodeBfsSeen.add(target);
                nodeDepth.set(target, nodeDepth.get(cur)! + 1);
                nodeBfsQ.push(target);
            }
        }
    }
    const maxNodeD = Math.max(0, ...nodeDepth.values());
    for (const n of rawNodes)
        if (!nodeDepth.has(n.id)) nodeDepth.set(n.id, maxNodeD + 1);

    const depthCols = new Map<number, string[]>();
    for (const [fp, d] of fileDepth) {
        if (!depthCols.has(d)) depthCols.set(d, []);
        depthCols.get(d)!.push(fp);
    }

    const CLUSTER_GAP_X = 450;
    const CLUSTER_GAP_Y = 180;
    const CLUSTER_PADDING = 50;

    const clusterBounds = new Map<string, { x: number; y: number; width: number; height: number }>();
    for (const [depth, files] of [...depthCols.entries()].sort((a, b) => a[0] - b[0])) {
        let yOffset = 60;
        for (const fp of files) {
            const nodes = fileGroups.get(fp) ?? [];
            const clusterHeight = nodes.length * (NODE_H + NODE_GAP) + CLUSTER_PADDING * 2;
            const clusterWidth = NODE_W + CLUSTER_PADDING * 2;
            clusterBounds.set(fp, {
                x: 60 + depth * CLUSTER_GAP_X,
                y: yOffset,
                width: clusterWidth,
                height: clusterHeight,
            });
            yOffset += clusterHeight + CLUSTER_GAP_Y;
        }
    }

    const pos = new Map<string, { x: number; y: number }>();
    for (const [fp, nodes] of fileGroups) {
        const cluster = clusterBounds.get(fp);
        if (!cluster) continue;
        const sortedNodes = [...nodes].sort((a, b) => {
            const la = callLine.get(a.id) ?? a.data.start_line ?? 0;
            const lb = callLine.get(b.id) ?? b.data.start_line ?? 0;
            return la - lb;
        });
        sortedNodes.forEach((n, idx) => {
            pos.set(n.id, {
                x: cluster.x + CLUSTER_PADDING,
                y: cluster.y + CLUSTER_PADDING + idx * (NODE_H + NODE_GAP),
            });
        });
    }

    const rfNodes: Node[] = rawNodes.map((n) => {
        const fp = nodeToFile.get(n.id) ?? 'unknown';
        const depth = nodeDepth.get(n.id) ?? maxNodeD + 1;
        return {
            id: n.id,
            type: 'ez',
            position: pos.get(n.id) ?? { x: 0, y: 0 },
            data: {
                ...n.data,
                isEntry: n.id === entry.id,
                cluster: fp,
                clusterColor: getClusterColor(fp, fileDepth.get(fp) ?? maxD + 1),
                depth,
                fileDepth: fileDepth.get(fp) ?? maxD + 1,
                isFocused: false,
                isCalled: false,
                isCaller: false,
                isDim: false,
            },
            width: NODE_W,
            height: NODE_H,
            draggable: true,
            selectable: false,
            focusable: false,
        };
    });

    const rfEdges: Edge[] = rawEdges.map((e) => {
        const sf = nodeToFile.get(e.source);
        const tf = nodeToFile.get(e.target);
        return {
            id: e.id,
            source: e.source,
            target: e.target,
            type: 'ez',
            data: {
                line: callLine.get(e.target) ?? 0,
                inTree: fileDepth.has(sf ?? '') && fileDepth.has(tf ?? ''),
                isCrossCluster: sf !== tf,
                sourceCluster: sf,
                targetCluster: tf,
            },
            animated: false,
            focusable: false,
        };
    });

    const clusters = Array.from(clusterBounds.entries()).map(([filepath, bounds]) => {
        const depth = fileDepth.get(filepath) ?? maxD + 1;
        return {
            id: `cluster-${filepath}`,
            filepath,
            bounds,
            depth,
            color: getClusterColor(filepath, depth),
            isEntry: filepath === entryFile,
            nodeCount: fileGroups.get(filepath)?.length ?? 0,
        };
    });

    return { nodes: rfNodes, edges: rfEdges, clusters };
}

// ─── architectLayout ──────────────────────────────────────────────────────────

export function architectLayout(
    rawNodes: RN[],
    rawEdges: RE[]
): { nodes: Node[]; edges: Edge[] } {
    if (!rawNodes.length) return { nodes: [], edges: [] };

    const groups = new Map<string, RN[]>();
    for (const n of rawNodes) {
        const fp = (n.data.filepath as string) ?? 'unknown';
        if (!groups.has(fp)) groups.set(fp, []);
        groups.get(fp)!.push(n);
    }

    const nodeToFile = new Map<string, string>();
    for (const n of rawNodes) nodeToFile.set(n.id, (n.data.filepath as string) ?? 'unknown');

    const fileAdj = new Map<string, Set<string>>();
    for (const e of rawEdges) {
        const sf = nodeToFile.get(e.source);
        const tf = nodeToFile.get(e.target);
        if (sf && tf && sf !== tf) {
            if (!fileAdj.has(sf)) fileAdj.set(sf, new Set());
            fileAdj.get(sf)!.add(tf);
        }
    }

    const filePaths = [...groups.keys()];
    const entryFile =
        filePaths.find((fp) => {
            const base = fp.replace(/\\/g, '/').split('/').pop()?.split('.')[0]?.toLowerCase() ?? '';
            return ENTRY_KW.has(base);
        }) ?? filePaths[0];

    const fileDepth = new Map<string, number>([[entryFile, 0]]);
    const bfsQ = [entryFile];
    const bfsSeen = new Set([entryFile]);
    while (bfsQ.length) {
        const cur = bfsQ.shift()!;
        for (const next of fileAdj.get(cur) ?? new Set()) {
            if (!bfsSeen.has(next)) {
                bfsSeen.add(next);
                fileDepth.set(next, fileDepth.get(cur)! + 1);
                bfsQ.push(next);
            }
        }
    }
    const maxD = Math.max(0, ...fileDepth.values());
    for (const fp of filePaths) if (!fileDepth.has(fp)) fileDepth.set(fp, maxD + 1);

    const cols = new Map<number, string[]>();
    for (const [fp, d] of fileDepth) {
        if (!cols.has(d)) cols.set(d, []);
        cols.get(d)!.push(fp);
    }

    const filePos = new Map<string, { x: number; y: number; h: number }>();
    for (const [d, fps] of [...cols.entries()].sort((a, b) => a[0] - b[0])) {
        let y = 60;
        for (const fp of fps) {
            const memberCount = groups.get(fp)?.length ?? 0;
            const h = HEADER_H + memberCount * MEMBER_H + 10;
            filePos.set(fp, { x: 60 + d * FILE_COL_W, y, h });
            y += h + FILE_ROW_GAP;
        }
    }

    const rfNodes: Node[] = [];
    for (const [fp, members] of groups) {
        const p = filePos.get(fp)!;
        const filename = fp.replace(/\\/g, '/').split('/').pop() ?? fp;
        const depth = fileDepth.get(fp) ?? maxD + 1;
        const { label: langLabel, color: langColor } = langInfo(filename);
        rfNodes.push({
            id: `file::${fp}`,
            type: 'fileGroup',
            position: { x: p.x, y: p.y },
            data: {
                filepath: fp,
                label: filename,
                langLabel,
                langColor,
                isEntry: fp === entryFile,
                depth,
                isFocused: false,
                isCalled: false,
                isCaller: false,
                isDim: false,
                members: members.map((n) => ({
                    id: n.id,
                    name: n.data.label as string,
                    type: n.data.type as string,
                    code: n.data.code as string,
                    start_line: n.data.start_line as number,
                    end_line: n.data.end_line as number,
                    filepath: fp,
                    hasHidden: Boolean((n.data as any)?.hasHidden),
                })),
                hasAnyHidden: members.some((n) => (n.data as any)?.hasHidden),
            },
            width: FILE_NODE_W,
            height: p.h,
            draggable: true,
            selectable: false,
            focusable: false,
        });
    }

    const edgeSeen = new Set<string>();
    const rfEdges: Edge[] = [];
    for (const [sf, targets] of fileAdj) {
        for (const tf of targets) {
            const eid = `fe::${sf}::${tf}`;
            if (!edgeSeen.has(eid)) {
                edgeSeen.add(eid);
                rfEdges.push({
                    id: eid,
                    source: `file::${sf}`,
                    target: `file::${tf}`,
                    type: 'ez',
                    data: { inTree: true, line: 0 },
                    animated: false,
                    focusable: false,
                });
            }
        }
    }
    return { nodes: rfNodes, edges: rfEdges };
}

// ─── applyEdgeFocus ───────────────────────────────────────────────────────────

export function applyEdgeFocus(edges: Edge[], focusId: string | null): Edge[] {
    if (!focusId) {
        return edges.map((e) => {
            const isCrossCluster = (e.data as any)?.isCrossCluster;
            const expandPulse = (e.data as any)?._expandPulse;
            return {
                ...e,
                data: {
                    ...e.data,
                    _active: false,
                    _animated: false,
                    _expandPulse: expandPulse,
                    _color: isCrossCluster
                        ? 'rgba(139,92,246,0.55)'
                        : (e.data as any)?.inTree ? 'var(--c4)' : 'var(--c3)',
                    _sw: isCrossCluster ? 2 : 1,
                    _so: isCrossCluster ? 0.6 : (e.data as any)?.inTree ? 0.5 : 0.18,
                },
                style: isCrossCluster ? { strokeDasharray: '5,5' } : undefined,
            };
        });
    }

    return edges.map((e) => {
        const isOut = e.source === focusId;
        const isIn = e.target === focusId;
        const active = isOut || isIn;
        const isCrossCluster = (e.data as any)?.isCrossCluster;
        const expandPulse = (e.data as any)?._expandPulse;

        return {
            ...e,
            data: {
                ...e.data,
                _active: active,
                _animated: active,
                _expandPulse: expandPulse,
                _color: active
                    ? isOut ? 'var(--bl)' : 'var(--tl)'
                    : isCrossCluster ? 'rgba(139,92,246,0.55)' : 'var(--c3)',
                _sw: active ? 2 : isCrossCluster ? 2 : 1,
                _so: active ? 0.85 : isCrossCluster ? 0.6 : 0.1,
            },
            style: isCrossCluster ? { strokeDasharray: '5,5' } : undefined,
        };
    });
}
