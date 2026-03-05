import { Node, Edge } from 'reactflow';

export interface RN {
    id: string;
    data: Record<string, any>;
}

export interface RE {
    id: string;
    source: string;
    target: string;
    label?: string;
    data?: Record<string, any>;
}

export interface MemberData {
    id: string;
    name: string;
    type: string;
    code: string;
    start_line: number;
    end_line: number;
    filepath: string;
}

export interface ClusterMetadata {
    id: string;
    filepath: string;
    bounds: { x: number; y: number; width: number; height: number };
    color: string;
    isEntry: boolean;
    nodeCount: number;
}
