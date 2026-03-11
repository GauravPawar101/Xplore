// API and WebSocket base URLs
// In dev mode, Vite proxies /jobs, /graph, /ws/* etc. to the backend,
// so we use relative URLs (empty base) to avoid cross-origin connection limits.
// Set VITE_API_URL in .env to override (e.g. for production builds).
const _api = typeof import.meta !== 'undefined' && (import.meta as any).env?.VITE_API_URL;
export const API_BASE = (_api && typeof _api === 'string') ? _api.replace(/\/$/, '') : '';
export const WS_BASE = (_api && typeof _api === 'string')
    ? _api.replace(/^http/, 'ws')
    : `ws://${typeof window !== 'undefined' ? window.location.host : 'localhost:5173'}`;

export const LEVEL_W = 300; // px between tree depth columns
export const LINE_SCALE = 2.2; // px per source-line unit on Y axis
export const NODE_W = 234;
export const NODE_H = 80;
export const NODE_GAP = 26; // minimum vertical gap between siblings
export const ENTRY_KW = new Set([
    'main',
    'index',
    'app',
    'run',
    'start',
    'init',
    'setup',
    '__main__',
    'server',
    'cli',
]);

// Architecture layout constants
export const FILE_COL_W = 340;
export const FILE_ROW_GAP = 28;
export const MEMBER_H = 22;
export const HEADER_H = 88;
export const FILE_NODE_W = 280;
