import React, {
  useState,
  useCallback,
  useRef,
  useEffect,
  useMemo,
} from 'react';
import ReactFlow, {
  ReactFlowProvider,
  useNodesState,
  useEdgesState,
  useReactFlow,
  useViewport,
  Background,
  BackgroundVariant,
  Panel,
  Node,
  Edge,
} from 'reactflow';
import 'reactflow/dist/base.css';
import { PromptBar } from '@/components/Promptbar';
import { VoiceSelector } from '@/components/VoiceSelector';
import {
  Play,
  Terminal,
  Activity,
  X,
  Cpu,
  GitBranch,
  Search,
  AlertCircle,
  UploadCloud,
  Volume2,
  VolumeX,
  Plus,
  Microscope,
  Layers,
} from 'lucide-react';
import axios from 'axios';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { motion, AnimatePresence } from 'framer-motion';
import ReactMarkdown from 'react-markdown';

import './CodeMap.css';
import { API_BASE, WS_BASE } from '@/config/constants';
import { treeLayout, applyEdgeFocus } from '@/lib/layoutUtils';
import { EzNode } from '@/components/EzNode';
import { EzEdge } from '@/components/EzEdge';
import { FileGroupNode } from '@/components/FileGroupNode';
import { FocusCtx, MemberClickCtx } from '@/components/context';
import { useTour } from '@/context/TourContext';

const NODE_TYPES: Record<string, React.ComponentType<any>> = {
  ez: EzNode,
  fileGroup: FileGroupNode,
};
const EDGE_TYPES: Record<string, React.ComponentType<any>> = { ez: EzEdge };
const ANALYZE_MAX_FILES = 0;

function EzDocsInner() {
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [clusters, setClusters] = useState<any[]>([]);
  const { fitView } = useReactFlow();
  const viewport = useViewport();

  const masterNodes = useRef<Node[]>([]);
  const masterEdges = useRef<Edge[]>([]);

  const [focusId, setFocusId] = useState<string | null>(null);
  const [outSet, setOutSet] = useState<Set<string>>(new Set());
  const [inSet, setInSet] = useState<Set<string>>(new Set());
  const focusCtxValue = useMemo(
    () => ({ focusId, outSet, inSet }),
    [focusId, outSet, inSet]
  );

  const [selectedNode, setSelectedNode] = useState<Node | null>(null);
  const [mode, setMode] = useState<'github' | 'upload'>('github');
  const [path, setPath] = useState('');

  // Track active codebase for background explanation polling
  const explanationPollRef = useRef<number | null>(null);
  const activeCodebaseRef = useRef<string | null>(null);
  const zipFileRef = useRef<HTMLInputElement>(null);
  const [savedAnalyses, setSavedAnalyses] = useState<{ codebase_id: string; source_path: string; created_at: string | null }[]>([]);
  const [busy, setBusy] = useState(false);
  // FIX: keep a ref in sync with busy state so WS closures can read it without going stale
  const busyRef = useRef(false);
  useEffect(() => { busyRef.current = busy; }, [busy]);

  const [busyMessage, setBusyMessage] = useState('');
  const [error, setError] = useState('');
  const [toasts, setToasts] = useState<{ id: string; message: string }[]>([]);
  const [stats, setStats] = useState({ n: 0, e: 0 });
  const [explanation, setExplanation] = useState('');
  const [aiStreaming, setAiStreaming] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [visibleNodeIds, setVisibleNodeIds] = useState<Set<string>>(new Set());
  const [loaderPointer, setLoaderPointer] = useState({ x: 0, y: 0 });
  const [selectedVoice, setSelectedVoice] = useState<string | null>(null);

  // Codebase Tour narrator
  const [narration, setNarration] = useState('');
  const [isNarrating, setIsNarrating] = useState(false);
  const [showNarrator, setShowNarrator] = useState(false);
  const [narratorSpeechOn, setNarratorSpeechOn] = useState(true);

  // Per-node narrator
  const [nodeNarration, setNodeNarration] = useState('');
  const [isNodeNarrating, setIsNodeNarrating] = useState(false);
  const [showNodeNarrator, setShowNodeNarrator] = useState(false);
  const [nodeNarratorSpeechOn, setNodeNarratorSpeechOn] = useState(true);

  // TTS refs — codebase tour
  const narratorSpeechRef = useRef(true);
  const sentenceQueue = useRef<string[]>([]);
  const ttsActive = useRef(false);
  const ttsChunkBuf = useRef('');

  // TTS refs — per-node
  const nodeNarratorSpeechRef = useRef(true);
  const nodeSentenceQueue = useRef<string[]>([]);
  const nodeTtsActive = useRef(false);
  const nodeTtsChunkBuf = useRef('');

  // Per-narrator cancelled flags — set true by stopSpeech, drain loops check these
  const ttsCancelled = useRef(false);
  const nodeTtsCancelled = useRef(false);

  useEffect(() => { narratorSpeechRef.current = narratorSpeechOn; }, [narratorSpeechOn]);
  useEffect(() => { nodeNarratorSpeechRef.current = nodeNarratorSpeechOn; }, [nodeNarratorSpeechOn]);
  useEffect(() => () => window.speechSynthesis?.cancel(), []);

  // FIX: clean up expand pulse timeout on unmount
  const expandPulseTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (expandPulseTimeoutRef.current) clearTimeout(expandPulseTimeoutRef.current);
  }, []);

  const pushToast = useCallback((message: string) => {
    const id = crypto.randomUUID();
    setToasts(prev => [...prev, { id, message }]);
    window.setTimeout(() => {
      setToasts(prev => prev.filter(t => t.id !== id));
    }, 5200);
  }, []);

  const describeRequestError = useCallback((err: any, fallback: string) => {
    const status = err?.response?.status;
    if (!status) return 'Backend connection lost. The current request was interrupted.';
    if ([499, 502, 503, 504].includes(status)) {
      return err?.response?.data?.detail || 'Backend connection lost. The current request was interrupted.';
    }
    return err?.response?.data?.detail ?? err?.message ?? fallback;
  }, []);

  const reportError = useCallback((message: string) => {
    setError(message);
    pushToast(message);
  }, [pushToast]);

  const onLoaderMouseMove = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width - 0.5) * 2;
    const y = ((e.clientY - rect.top) / rect.height - 0.5) * 2;
    setLoaderPointer({ x: Math.max(-1, Math.min(1, x)), y: Math.max(-1, Math.min(1, y)) });
  }, []);

  const resetLoaderPointer = useCallback(() => setLoaderPointer({ x: 0, y: 0 }), []);

  // ── Stable focus applier (always reads fresh refs, safe in WS closures) ──
  // FIX: added stable [] dependency array — the ref assignment is the escape hatch,
  // so this only needs to run once. Reading masterNodes/masterEdges through refs is safe.
  const applyFocusRef = useRef<(nodeId: string) => void>(() => { });
  useEffect(() => {
    applyFocusRef.current = (nodeId: string) => {
      const node = masterNodes.current.find(n => n.id === nodeId);
      if (!node) return;

      const newOut = new Set(
        masterEdges.current.filter(e => e.source === nodeId).map(e => e.target)
      );
      const newIn = new Set(
        masterEdges.current.filter(e => e.target === nodeId).map(e => e.source)
      );

      setFocusId(nodeId);
      setOutSet(newOut);
      setInSet(newIn);

      setNodes((nodes) =>
        nodes.map((n) => {
          const id = n.id;
          const isFocused = id === nodeId;
          const isCalled = newOut.has(id);
          const isCaller = newIn.has(id);
          const isDim = !isFocused && !isCalled && !isCaller;
          const d = n.data as Record<string, unknown>;
          if (d.isFocused === isFocused && d.isCalled === isCalled && d.isCaller === isCaller && d.isDim === isDim)
            return n;
          return { ...n, data: { ...d, isFocused, isCalled, isCaller, isDim } };
        })
      );
      setEdges(applyEdgeFocus(masterEdges.current, nodeId));
      fitView({ nodes: [node], duration: 500, padding: 0.35 });
    };
  }); // intentionally runs each render so closure captures latest setNodes/setEdges/fitView

  const clearFocusRef = useRef<() => void>(() => { });
  useEffect(() => {
    clearFocusRef.current = () => {
      setFocusId(null);
      setOutSet(new Set());
      setInSet(new Set());
      setNodes((nodes) =>
        nodes.map((n) => {
          const d = n.data as Record<string, unknown>;
          if (!d.isFocused && !d.isCalled && !d.isCaller && !d.isDim) return n;
          return { ...n, data: { ...d, isFocused: false, isCalled: false, isCaller: false, isDim: false } };
        })
      );
      setEdges(applyEdgeFocus(masterEdges.current, null));
      setSelectedNode(null);
    };
  }); // intentionally runs each render so closure captures latest setNodes/setEdges

  // ── TTS helpers ───────────────────────────────────────────────────────
  const getPreferredVoice = useCallback(() => {
    const voices = window.speechSynthesis.getVoices();
    if (!voices.length) return null;
    const selected = voices.find(v => v.name === selectedVoice);
    if (selected) return selected;
    const preferred = voices.find(v =>
      /natural|neural|premium|google|microsoft|samantha|daniel|karen|moira|alex|siri|enhanced/i.test(v.name)
    );
    return preferred ?? voices.find(v => v.default) ?? voices[0];
  }, [selectedVoice]);

  useEffect(() => {
    if (window.speechSynthesis.getVoices().length > 0) return;
    const onVoicesChanged = () => { };
    window.speechSynthesis.onvoiceschanged = onVoicesChanged;
    return () => { window.speechSynthesis.onvoiceschanged = null; };
  }, []);

  // FIX: drainQueue now returns a stable drain *function* (not a factory-of-factory).
  // Previously the return value of drainQueue was itself a factory that returned drain,
  // meaning drainSentenceQueue was actually the inner `drain` factory, not the drainer.
  const drainQueue = useCallback(
    (
      queue: React.MutableRefObject<string[]>,
      active: React.MutableRefObject<boolean>,
      cancelled: React.MutableRefObject<boolean>,
    ) => {
      const drain = () => {
        if (cancelled.current) { active.current = false; return; }
        if (queue.current.length === 0) { active.current = false; return; }
        active.current = true;
        const utt = new SpeechSynthesisUtterance(queue.current.shift()!);
        utt.rate = 0.98;
        utt.pitch = 1;
        utt.volume = 1;
        const voice = getPreferredVoice();
        if (voice) utt.voice = voice;
        utt.onend = drain;
        utt.onerror = drain;
        window.speechSynthesis.speak(utt);
      };
      // FIX: return drain directly, not a function that returns drain
      return drain;
    },
    [getPreferredVoice]
  );

  // FIX: use useRef + useEffect instead of useMemo so these are stable across renders
  // and always point to the correct queue/active/cancelled refs.
  const drainSentenceQueueRef = useRef<() => void>(() => { });
  const drainNodeSentenceQueueRef = useRef<() => void>(() => { });

  useEffect(() => {
    drainSentenceQueueRef.current = drainQueue(sentenceQueue, ttsActive, ttsCancelled);
  }, [drainQueue]);

  useEffect(() => {
    drainNodeSentenceQueueRef.current = drainQueue(nodeSentenceQueue, nodeTtsActive, nodeTtsCancelled);
  }, [drainQueue]);

  // Stable wrappers that always delegate to the current ref
  const drainSentenceQueue = useCallback(() => drainSentenceQueueRef.current(), []);
  const drainNodeSentenceQueue = useCallback(() => drainNodeSentenceQueueRef.current(), []);

  const enqueueChunk = useCallback((
    raw: string,
    speechRef: React.MutableRefObject<boolean>,
    buf: React.MutableRefObject<string>,
    queue: React.MutableRefObject<string[]>,
    active: React.MutableRefObject<boolean>,
    drain: () => void,
  ) => {
    if (!speechRef.current || !window.speechSynthesis) return;
    const clean = raw
      .replace(/[\u{1F000}-\u{1FFFF}]/gu, '')
      .replace(/[\u{2600}-\u{27BF}]/gu, '')
      .replace(/[\u{FE00}-\u{FEFF}]/gu, '')
      .replace(/#{1,6}\s*/g, '')
      .replace(/\*\*/g, '')
      .replace(/\*/g, '')
      .replace(/_/g, ' ')
      .replace(/`[^`]*`/g, '')
      .replace(/`/g, '')
      .replace(/https?:\/\/\S+/g, '')
      .replace(/[-–—]{2,}/g, ', ')
      .replace(/[|\\[\]{}()<>]/g, ' ')
      .replace(/\n+/g, ' ')
      .replace(/\s{2,}/g, ' ')
      .trim();
    buf.current += clean;
    const parts = buf.current.split(/(?<=[.!?])\s+/);
    buf.current = parts.pop() ?? '';
    for (const s of parts) { if (s.trim()) queue.current.push(s.trim()); }
    if (!active.current) drain();
  }, []);

  const stopSpeech = useCallback((
    queue: React.MutableRefObject<string[]>,
    buf: React.MutableRefObject<string>,
    active: React.MutableRefObject<boolean>,
    cancelled: React.MutableRefObject<boolean>,
    otherActive: React.MutableRefObject<boolean>,
  ) => {
    cancelled.current = true;
    if (active.current && !otherActive.current) {
      window.speechSynthesis?.cancel();
    }
    queue.current = []; buf.current = ''; active.current = false;
  }, []);

  const stopNarratorSpeech = useCallback(
    () => stopSpeech(sentenceQueue, ttsChunkBuf, ttsActive, ttsCancelled, nodeTtsActive), [stopSpeech]
  );
  const stopNodeNarratorSpeech = useCallback(
    () => stopSpeech(nodeSentenceQueue, nodeTtsChunkBuf, nodeTtsActive, nodeTtsCancelled, ttsActive), [stopSpeech]
  );

  const toggleNarratorSpeech = useCallback(() => {
    setNarratorSpeechOn(p => { if (p) stopNarratorSpeech(); return !p; });
  }, [stopNarratorSpeech]);

  const toggleNodeNarratorSpeech = useCallback(() => {
    setNodeNarratorSpeechOn(p => { if (p) stopNodeNarratorSpeech(); return !p; });
  }, [stopNodeNarratorSpeech]);

  const toggleSpeech = useCallback(() => {
    if (isSpeaking) { window.speechSynthesis.cancel(); setIsSpeaking(false); }
    else if (explanation) {
      const utt = new SpeechSynthesisUtterance(explanation.replace(/[#*`_]/g, ''));
      utt.rate = 0.98;
      utt.pitch = 1;
      const voice = getPreferredVoice();
      if (voice) utt.voice = voice;
      utt.onend = () => setIsSpeaking(false);
      utt.onerror = () => setIsSpeaking(false);
      window.speechSynthesis.speak(utt);
      setIsSpeaking(true);
    }
  }, [isSpeaking, explanation, getPreferredVoice]);

  const bufRef = useRef<Map<string, any>>(new Map());
  const flushTs = useRef(0);
  const rawRef = useRef<{ nodes: any[]; edges: any[] }>({ nodes: [], edges: [] });
  const expandPulseRef = useRef<{ nodeIds: Set<string>; edgeIds: Set<string> } | null>(null);

  const expandNode = useCallback((nodeId: string) => {
    setVisibleNodeIds(prev => {
      const next = new Set(prev);
      const added: string[] = [];
      let count = 0;

      for (const e of rawRef.current.edges) {
        if (e.source === nodeId && !next.has(e.target) && count < 50) {
          next.add(e.target); added.push(e.target); count++;
        }
      }
      for (const e of rawRef.current.edges) {
        if (e.target === nodeId && !next.has(e.source) && count < 60) {
          next.add(e.source); added.push(e.source); count++;
        }
      }
      const addedFiles = new Set(
        added.map(id => rawRef.current.nodes.find((n: any) => n.id === id)?.data?.filepath).filter(Boolean)
      );
      for (const n of rawRef.current.nodes) {
        if (!next.has(n.id) && addedFiles.has(n.data?.filepath) && count < 80) {
          next.add(n.id); added.push(n.id); count++;
        }
      }
      if (added.length === 0) {
        const sourceNode = rawRef.current.nodes.find((n: any) => n.id === nodeId);
        const sourceLayer = Number(sourceNode?.data?.layer ?? 0);
        for (const n of rawRef.current.nodes) {
          const layer = Number(n?.data?.layer ?? 2);
          if (!next.has(n.id) && layer === sourceLayer + 1 && count < 50) {
            next.add(n.id); added.push(n.id); count++;
          }
        }
      }
      if (added.length > 0) {
        const addedSet = new Set(added);
        const newEdgeIds = new Set(
          rawRef.current.edges
            .filter((ee: any) => (ee.source === nodeId && addedSet.has(ee.target)) || (addedSet.has(ee.source) && addedSet.has(ee.target)))
            .map((ee: any) => ee.id)
        );
        expandPulseRef.current = { nodeIds: new Set(added), edgeIds: newEdgeIds };
      }
      return next;
    });
  }, []);

  const expandFromNodes = useCallback((sourceNodeIds: string[]) => {
    if (!sourceNodeIds.length) return;
    setVisibleNodeIds(prev => {
      const next = new Set(prev);
      const added: string[] = [];
      const sourceSet = new Set(sourceNodeIds);
      let count = 0;
      for (const e of rawRef.current.edges) {
        if (sourceSet.has(e.source) && !next.has(e.target) && count < 80) {
          next.add(e.target); added.push(e.target); count++;
        }
      }
      if (added.length > 0) {
        const addedSet = new Set(added);
        const newEdgeIds = new Set(
          rawRef.current.edges
            .filter((ee: any) => sourceSet.has(ee.source) && addedSet.has(ee.target))
            .map((ee: any) => ee.id)
        );
        expandPulseRef.current = { nodeIds: new Set(added), edgeIds: newEdgeIds };
      }
      return next;
    });
  }, []);

  // expandNextLayer, expandAll, collapseToRoot are available for future toolbar use
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const _expandNextLayer = useCallback(() => {
    setVisibleNodeIds(prev => {
      const next = new Set(prev);
      let maxVisibleLayer = -1;
      for (const n of rawRef.current.nodes) {
        if (prev.has(n.id)) {
          const layer = Number(n.data?.layer ?? 0);
          if (layer > maxVisibleLayer) maxVisibleLayer = layer;
        }
      }
      const targetLayer = maxVisibleLayer + 1;
      const added: string[] = [];
      for (const n of rawRef.current.nodes) {
        const layer = Number(n.data?.layer ?? 2);
        if (!next.has(n.id) && layer <= targetLayer) { next.add(n.id); added.push(n.id); }
      }
      if (added.length > 0) {
        const addedSet = new Set(added);
        const newEdgeIds = new Set(
          rawRef.current.edges
            .filter((ee: any) => addedSet.has(ee.source) || addedSet.has(ee.target))
            .map((ee: any) => ee.id)
        );
        expandPulseRef.current = { nodeIds: new Set(added), edgeIds: newEdgeIds };
      }
      return next;
    });
  }, []);

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const _expandAll = useCallback(() => {
    setVisibleNodeIds(() => {
      const next = new Set<string>();
      for (const n of rawRef.current.nodes) next.add(n.id);
      return next;
    });
  }, []);

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const _collapseToRoot = useCallback(() => {
    if (rawRef.current.nodes.length === 0) return;
    const rn = rawRef.current.nodes;
    const re = rawRef.current.edges;
    const vis = new Set<string>();
    const rootNodes = rn.filter((n: any) => n.data?.layer === 0 || n.data?.is_root_file);
    if (rootNodes.length > 0) {
      for (const n of rootNodes) vis.add(n.id);
      let count = 0;
      for (const e of re) {
        if (vis.has(e.source) && !vis.has(e.target) && count < 80) { vis.add(e.target); count++; }
      }
    } else {
      const entry = rn.find((n: any) => n.data?.isEntry) ?? rn[0];
      if (entry) {
        vis.add(entry.id);
        let count = 0;
        for (const e of re) {
          if (e.source === entry.id && !vis.has(e.target) && count < 60) { vis.add(e.target); count++; }
        }
      }
      if (vis.size === 0) for (let i = 0; i < Math.min(80, rn.length); i++) vis.add(rn[i].id);
    }
    setVisibleNodeIds(vis);
  }, []);

  // ── Commit graph ──────────────────────────────────────────────────────
  // FIX: removed `visibleNodeIds` from deps. commit always receives `visible` as a param,
  // so it never needs to close over the state. The useEffect below passes it explicitly.
  // This breaks the circular dependency: visibleNodeIds → commit → useEffect → commit.
  const commit = useCallback(
    (rn: any[], re: any[], visible: Set<string>) => {
      rawRef.current = { nodes: rn, edges: re };
      const fn = rn.filter(n => visible.has(n.id) || n.type === 'fileGroup');
      const fe = re.filter(e => visible.has(e.source) && visible.has(e.target));
      const hiddenMap = new Set<string>();
      for (const e of re) { if (visible.has(e.source) && !visible.has(e.target)) hiddenMap.add(e.source); }
      const maxLayer = Math.max(0, ...rn.map((n: any) => Number(n?.data?.layer ?? 0)));
      const hasAnyHiddenNodes = rn.some((n: any) => !visible.has(n.id));
      if (hasAnyHiddenNodes) {
        for (const n of fn) {
          const layer = Number((n as any)?.data?.layer ?? maxLayer);
          if (layer < maxLayer) hiddenMap.add((n as any).id);
        }
      }
      const fnWithHidden = fn.map(n => ({ ...n, data: { ...n.data, hasHidden: hiddenMap.has(n.id) } }));
      const layoutResult = treeLayout(fnWithHidden, fe);
      let { nodes: ln, edges: le, clusters: lc } = layoutResult;

      const pulse = expandPulseRef.current;
      if (pulse) {
        ln = ln.map((n: Node) => ({ ...n, data: { ...n.data, _expandPulse: pulse.nodeIds.has(n.id) } }));
        le = le.map((e: Edge) => ({ ...e, data: { ...e.data, _expandPulse: pulse.edgeIds.has(e.id) } }));
        expandPulseRef.current = null;
        if (expandPulseTimeoutRef.current) clearTimeout(expandPulseTimeoutRef.current);
        expandPulseTimeoutRef.current = setTimeout(() => {
          expandPulseTimeoutRef.current = null;
          setNodes((prev: Node[]) => prev.map(n => ({ ...n, data: { ...n.data, _expandPulse: false } })));
          setEdges((prev: Edge[]) => prev.map(e => ({ ...e, data: { ...e.data, _expandPulse: false } })));
        }, 2400);
      }

      masterNodes.current = ln;
      masterEdges.current = le;
      setNodes(ln);
      setEdges(applyEdgeFocus(le, null));
      setClusters(lc || []);
      setStats({ n: ln.length, e: le.length });
      setFocusId(null); setOutSet(new Set()); setInSet(new Set()); setSelectedNode(null);

      requestAnimationFrame(() => {
        fitView({ nodes: ln.length ? ln : undefined, duration: 500, padding: 0.18 });
      });
    },
    [setNodes, setEdges, fitView]
  );

  // FIX: pass visibleNodeIds explicitly into commit so commit doesn't need it as a dep
  useEffect(() => {
    if (rawRef.current.nodes.length > 0) commit(rawRef.current.nodes, rawRef.current.edges, visibleNodeIds);
  }, [visibleNodeIds, commit]);

  const clearFocus = useCallback(() => {
    clearFocusRef.current();
  }, []);

  // ── Node click ────────────────────────────────────────────────────────
  const onNodeClick = useCallback((_: any, node: Node) => {
    if (focusId === node.id) { clearFocus(); return; }
    const newOut = new Set(masterEdges.current.filter(e => e.source === node.id).map(e => e.target));
    const newIn = new Set(masterEdges.current.filter(e => e.target === node.id).map(e => e.source));
    setFocusId(node.id); setOutSet(newOut); setInSet(newIn);
    if (node.type !== 'fileGroup') { setSelectedNode(node); setExplanation(node?.data?.explanation ?? ''); }
    setEdges(applyEdgeFocus(masterEdges.current, node.id));
  }, [focusId, clearFocus, setEdges]);

  const onNodeDoubleClick = useCallback((_: any, node: Node) => {
    if (node.data.hasHidden) expandNode(node.id);
  }, [expandNode]);

  const onPaneClick = useCallback(() => { if (focusId) clearFocus(); }, [focusId, clearFocus]);

  // ── Background explanation polling ────────────────────────────────────
  const startExplanationPolling = useCallback((codebaseId: string) => {
    if (explanationPollRef.current) { clearTimeout(explanationPollRef.current); explanationPollRef.current = null; }
    activeCodebaseRef.current = codebaseId;
    let lastExplained = 0;
    let interval = 4000;

    const poll = async () => {
      if (activeCodebaseRef.current !== codebaseId) return;
      try {
        const { data } = await axios.get(`${API_BASE}/graph/explanations/status`, {
          params: { codebase_id: codebaseId },
        });
        const { total, explained } = data;
        if (explained > lastExplained) {
          lastExplained = explained;
          const graphRes = await axios.get(`${API_BASE}/graph`, { params: { codebase_id: codebaseId } });
          const freshNodes: any[] = graphRes.data?.nodes ?? [];
          const explanationMap = new Map<string, string>();
          for (const n of freshNodes) { if (n.data?.explanation) explanationMap.set(n.id, n.data.explanation); }
          masterNodes.current = masterNodes.current.map(n => {
            const newExp = explanationMap.get(n.id);
            return newExp && n.data.explanation !== newExp ? { ...n, data: { ...n.data, explanation: newExp } } : n;
          });
          setNodes(prev => prev.map(n => {
            const newExp = explanationMap.get(n.id);
            return newExp && n.data.explanation !== newExp ? { ...n, data: { ...n.data, explanation: newExp } } : n;
          }));
          setSelectedNode(prev => {
            if (!prev) return prev;
            const newExp = explanationMap.get(prev.id);
            if (newExp && prev.data.explanation !== newExp) {
              setExplanation(newExp);
              return { ...prev, data: { ...prev.data, explanation: newExp } };
            }
            return prev;
          });
        }
        if (explained < total) {
          interval = Math.min(interval * 1.2, 10000);
          explanationPollRef.current = window.setTimeout(poll, interval);
        } else {
          explanationPollRef.current = null;
        }
      } catch {
        interval = Math.min(interval * 1.5, 15000);
        explanationPollRef.current = window.setTimeout(poll, interval);
      }
    };
    explanationPollRef.current = window.setTimeout(poll, 5000);
  }, [setNodes]);

  useEffect(() => () => {
    if (explanationPollRef.current) clearTimeout(explanationPollRef.current);
  }, []);

  // ── Analyze ───────────────────────────────────────────────────────────
  const analyze = async () => {
    setBusy(true); setError(''); setNodes([]); setEdges([]);
    setBusyMessage('Scanning files in batches...');
    masterNodes.current = []; masterEdges.current = [];
    setFocusId(null); setOutSet(new Set()); setInSet(new Set());
    setSelectedNode(null); setExplanation(''); setStats({ n: 0, e: 0 });
    bufRef.current.clear();
    if (explanationPollRef.current) { clearTimeout(explanationPollRef.current); explanationPollRef.current = null; }
    activeCodebaseRef.current = null;

    const buildInitialVisible = (rn: any[], re: any[]) => {
      const vis = new Set<string>();
      const rootNodes = rn.filter((n: any) => n.data?.layer === 0 || n.data?.is_root_file);
      if (rootNodes.length > 0) {
        for (const n of rootNodes) vis.add(n.id);
        let count = 0;
        for (const e of re) {
          if (vis.has(e.source) && !vis.has(e.target) && count < 80) { vis.add(e.target); count++; }
        }
      } else {
        const entry = rn.find((n: any) => n.data?.isEntry) ?? rn[0];
        if (entry) {
          vis.add(entry.id);
          let count = 0;
          for (const e of re) {
            if (e.source === entry.id && !vis.has(e.target) && count < 60) { vis.add(e.target); count++; }
          }
        }
        if (vis.size === 0) for (let i = 0; i < Math.min(80, rn.length); i++) vis.add(rn[i].id);
      }
      return vis;
    };

    const applyGraphResult = (data: { nodes?: any[]; edges?: any[]; codebase_id?: string }) => {
      const rn = data.nodes ?? []; const re = data.edges ?? [];
      const vis = buildInitialVisible(rn, re);
      setVisibleNodeIds(vis);
      commit(rn, re, vis);
      if (data.codebase_id) startExplanationPolling(data.codebase_id);
    };

    const newCodebaseId = crypto.randomUUID();
    try {
      if (mode === 'upload') {
        const file = zipFileRef.current?.files?.[0];
        if (!file) { reportError('Select a .zip file first'); setBusy(false); return; }
        const formData = new FormData();
        formData.append('file', file);
        try {
          const uploadRes = await axios.post(`${API_BASE}/analyze/upload`, formData, {
            headers: { 'Content-Type': 'multipart/form-data' },
          });
          applyGraphResult(uploadRes.data);
        } catch (uploadErr: any) {
          reportError(describeRequestError(uploadErr, 'Upload analysis failed'));
        }
        setBusyMessage(''); setBusy(false);
        return;
      }

      const jobPayload = { url: path, max_files: ANALYZE_MAX_FILES, codebase_id: newCodebaseId };
      try {
        const jobRes = await axios.post(`${API_BASE}/jobs/analyze`, jobPayload);
        const jobId = jobRes.data?.job_id;
        if (jobId) {
          let pollInterval = 2000;
          const MAX_INTERVAL = 5000;

          const pollStatus = async (): Promise<void> => {
            try {
              const statusRes = await axios.get(`${API_BASE}/jobs/${jobId}/status`);
              const st = statusRes.data?.status;
              const progress = statusRes.data?.progress;
              if (progress) {
                const nextMessage = progress.message || progress.phase;
                if (nextMessage) setBusyMessage(nextMessage);
                setStats(prev => ({
                  n: Number(progress.node_count ?? prev.n ?? 0),
                  e: Number(progress.edge_count ?? prev.e ?? 0),
                }));
              }
              if (st === 'done') {
                const resultRes = await axios.get(`${API_BASE}/jobs/${jobId}/result`);
                const ref = resultRes.data;
                if (Array.isArray(ref?.nodes) && Array.isArray(ref?.edges)) {
                  applyGraphResult({ ...ref, codebase_id: ref.codebase_id || newCodebaseId });
                } else if (ref?.codebase_id) {
                  const graphRes = await axios.get(`${API_BASE}/graph?codebase_id=${encodeURIComponent(ref.codebase_id)}`);
                  applyGraphResult({ ...graphRes.data, codebase_id: ref.codebase_id });
                } else {
                  applyGraphResult(ref);
                }
                setBusyMessage(''); setBusy(false); return;
              }
              if (st === 'failed') {
                reportError(statusRes.data?.error ?? 'Analysis failed');
                setBusyMessage(''); setBusy(false); return;
              } else {
                pollInterval = Math.min(pollInterval * 1.3, MAX_INTERVAL);
                setTimeout(pollStatus, pollInterval);
              }
            } catch (pollErr: any) {
              reportError(describeRequestError(pollErr, 'Analysis interrupted while waiting for completion.'));
              setBusyMessage(''); setBusy(false);
            }
          };
          await pollStatus();
          return;
        }
      } catch (jobErr: any) {
        if (jobErr.response?.status === 503) {
          // Queue not configured — fall through to WS fallback
        } else {
          throw jobErr;
        }
      }

      // Fallback: WebSocket
      if (mode === 'github') {
        const ws = new WebSocket(`${WS_BASE}/ws/analyze/github`);
        let completed = false;
        ws.onopen = () => ws.send(JSON.stringify({ url: path }));
        ws.onmessage = ev => {
          const d = JSON.parse(ev.data);
          if (d.type === 'error') { reportError(d.message); setBusy(false); ws.close(); return; }
          if (d.type === 'update') {
            setBusyMessage('Streaming repository analysis...');
            for (const n of d.nodes ?? []) bufRef.current.set(n.id, n);
            const now = Date.now();
            if (now - flushTs.current > 500) { setStats(s => ({ ...s, n: bufRef.current.size })); flushTs.current = now; }
          }
          if (d.type === 'complete') {
            completed = true;
            applyGraphResult(d.graph ?? {});
            setBusyMessage(''); setBusy(false); ws.close();
          }
        };
        ws.onerror = () => { reportError('WebSocket error — backend running?'); setBusyMessage(''); setBusy(false); };
        ws.onclose = () => {
          // FIX: use busyRef instead of stale `busy` closure
          if (!completed && busyRef.current) {
            reportError('Backend connection closed before analysis completed.');
            setBusyMessage(''); setBusy(false);
          }
        };
        return;
      }
    } catch (e: any) {
      reportError(describeRequestError(e, 'Request failed')); setBusyMessage(''); setBusy(false);
    }
  };

  const loadSavedAnalyses = useCallback(() => {
    axios.get<{ codebase_id: string; source_path: string; created_at: string | null }[]>(`${API_BASE}/analyses`)
      .then(r => setSavedAnalyses(r.data ?? []))
      .catch(() => setSavedAnalyses([]));
  }, []);

  const loadGraphByCodebaseId = useCallback(async (id: string) => {
    setBusy(true); setError('');
    setBusyMessage('Loading saved graph...');
    try {
      const r = await axios.get(`${API_BASE}/graph?codebase_id=${encodeURIComponent(id)}`);
      const rn = r.data?.nodes ?? []; const re = r.data?.edges ?? [];
      const sorted = [...rn].sort((a: any, b: any) => {
        const la = Number(a.data?.layer ?? 2);
        const lb = Number(b.data?.layer ?? 2);
        if (la !== lb) return la - lb;
        const fpA = (a.data?.filepath ?? '').split(/[\/\\]/).length;
        const fpB = (b.data?.filepath ?? '').split(/[\/\\]/).length;
        return fpA - fpB;
      });
      const vis = new Set<string>();
      for (const n of sorted) vis.add(n.id);
      setVisibleNodeIds(vis);
      commit(rn, re, vis);
      startExplanationPolling(id);
    } catch (e: any) {
      reportError(describeRequestError(e, 'Load failed'));
    } finally {
      setBusyMessage(''); setBusy(false);
    }
  }, [commit, describeRequestError, reportError, startExplanationPolling]);

  // ── AI explain ────────────────────────────────────────────────────────
  const explain = useCallback(() => {
    if (!selectedNode) return;
    if (selectedNode.data?.explanation) {
      setExplanation(selectedNode.data.explanation);
      setAiStreaming(false);
      return;
    }
    setAiStreaming(true); setExplanation('');
    const edgeList = masterEdges.current;
    const nodeList = masterNodes.current;
    const getLabel = (id: string) => nodeList.find((n: any) => n.id === id)?.data?.label ?? id;
    const callees = edgeList.filter((e: any) => e.source === selectedNode.id).map((e: any) => getLabel(e.target));
    const callers = edgeList.filter((e: any) => e.target === selectedNode.id).map((e: any) => getLabel(e.source));
    const ws = new WebSocket(`${WS_BASE}/ws/explain`);
    ws.onopen = () => ws.send(JSON.stringify({
      code: selectedNode.data.code,
      context: selectedNode.data.filepath,
      callers,
      callees,
    }));
    ws.onmessage = ev => setExplanation(p => p + ev.data);
    ws.onclose = () => setAiStreaming(false);
    ws.onerror = () => { setExplanation('Unable to generate explanation. Check backend and Ollama or Hugging Face token.'); setAiStreaming(false); };
  }, [selectedNode]);

  // ── Generic WS narration handler ──────────────────────────────────────
  const makeNarrationHandler = useCallback((
    setText: React.Dispatch<React.SetStateAction<string>>,
    speechRef: React.MutableRefObject<boolean>,
    buf: React.MutableRefObject<string>,
    queue: React.MutableRefObject<string[]>,
    active: React.MutableRefObject<boolean>,
    drain: () => void,
  ) => (ev: MessageEvent) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'focus') {
        applyFocusRef.current(msg.node_id);
        return;
      }
      if (msg.type === 'text') {
        setText(p => p + msg.chunk);
        enqueueChunk(msg.chunk, speechRef, buf, queue, active, drain);
      }
    } catch { setText(p => p + ev.data); }
  }, [enqueueChunk]);

  // ── Codebase Tour ─────────────────────────────────────────────────────
  const { setNarrating: setTourNarrating, registerNarratorWs } = useTour();
  const startNarration = useCallback(() => {
    if (rawRef.current.nodes.length === 0) return;
    setIsNarrating(true); setNarration(''); setShowNarrator(true);
    setTourNarrating(true);
    stopNarratorSpeech(); ttsCancelled.current = false; sentenceQueue.current = []; ttsChunkBuf.current = '';

    const ws = new WebSocket(`${WS_BASE}/ws/narrate`);
    registerNarratorWs(ws);
    ws.onmessage = makeNarrationHandler(
      setNarration, narratorSpeechRef, ttsChunkBuf, sentenceQueue, ttsActive, drainSentenceQueue
    );
    ws.onclose = () => {
      registerNarratorWs(null);
      setTourNarrating(false);
      if (ttsChunkBuf.current.trim()) { sentenceQueue.current.push(ttsChunkBuf.current.trim()); ttsChunkBuf.current = ''; if (!ttsActive.current) drainSentenceQueue(); }
      setIsNarrating(false);
    };
    ws.onerror = () => { setNarration(p => p + '\n\n❌ Connection error.'); pushToast('Narration stream disconnected.'); registerNarratorWs(null); setTourNarrating(false); setIsNarrating(false); };
  }, [makeNarrationHandler, stopNarratorSpeech, drainSentenceQueue, setTourNarrating, registerNarratorWs, pushToast]);

  // ── Per-node narrator ─────────────────────────────────────────────────
  const startNodeNarration = useCallback(() => {
    if (!selectedNode) return;
    setIsNodeNarrating(true); setNodeNarration(''); setShowNodeNarrator(true);
    stopNodeNarratorSpeech(); nodeTtsCancelled.current = false; nodeSentenceQueue.current = []; nodeTtsChunkBuf.current = '';

    const ws = new WebSocket(`${WS_BASE}/ws/narrate/node`);
    ws.onopen = () => ws.send(JSON.stringify({ node_id: selectedNode.id }));
    ws.onmessage = makeNarrationHandler(
      setNodeNarration, nodeNarratorSpeechRef, nodeTtsChunkBuf, nodeSentenceQueue, nodeTtsActive, drainNodeSentenceQueue
    );
    ws.onclose = () => {
      if (nodeTtsChunkBuf.current.trim()) { nodeSentenceQueue.current.push(nodeTtsChunkBuf.current.trim()); nodeTtsChunkBuf.current = ''; if (!nodeTtsActive.current) drainNodeSentenceQueue(); }
      setIsNodeNarrating(false);
    };
    ws.onerror = () => { setNodeNarration(p => p + '\n\n❌ Connection error.'); pushToast('Node explanation stream disconnected.'); setIsNodeNarrating(false); };
  }, [selectedNode, makeNarrationHandler, stopNodeNarratorSpeech, drainNodeSentenceQueue, pushToast]);

  const syLang = (fp = '') =>
    fp.endsWith('.py') ? 'python'
      : fp.endsWith('.ts') || fp.endsWith('.tsx') ? 'typescript'
        : fp.endsWith('.java') ? 'java'
          : fp.endsWith('.rs') ? 'rust'
            : 'javascript';

  const onMemberClick = useCallback((m: any) => {
    // FIX: use actual node position from masterNodes if available to avoid focus position mismatch
    const existingNode = masterNodes.current.find(n => n.id === m.id);
    const position = existingNode?.position ?? { x: 0, y: 0 };
    setSelectedNode({
      id: m.id,
      type: m.type,
      position,
      data: {
        label: m.name,
        filepath: m.filepath,
        type: m.type,
        code: m.code,
        start_line: m.start_line,
        end_line: m.end_line,
        explanation: (m as any).explanation,
      },
    });
    setExplanation((m as any).explanation ?? '');
    const parentId = `file::${m.filepath}`;
    if (focusId !== parentId) {
      setFocusId(parentId);
      setOutSet(new Set(masterEdges.current.filter(e => e.source === parentId).map(e => e.target)));
      setInSet(new Set(masterEdges.current.filter(e => e.target === parentId).map(e => e.source)));
      setEdges(applyEdgeFocus(masterEdges.current, parentId));
    }
  }, [focusId, setEdges]);

  return (
    <FocusCtx.Provider value={focusCtxValue}>
      {/* FIX: VoiceSelector moved inside the flex container so it doesn't push graph down */}
      <div style={{ display: 'flex', height: '100vh', width: '100vw', overflow: 'hidden', position: 'relative' }}>

        {/* Voice selector floats over the top-right of the graph area */}
        <div style={{ position: 'absolute', top: 12, right: selectedNode ? 440 : 12, zIndex: 50 }}>
          <VoiceSelector selectedVoice={selectedVoice} onVoiceChange={setSelectedVoice} />
        </div>

        {/* ── SIDEBAR ─────────────────────────────────────────────────── */}
        <motion.div className="ez-sidebar" initial={{ x: -268, opacity: 0 }} animate={{ x: 0, opacity: 1 }} transition={{ duration: 0.32, ease: [0.22, 1, 0.36, 1] }}>
          <div style={{ padding: '13px 15px', borderBottom: '1px solid var(--ln)', display: 'flex', alignItems: 'center', gap: 9 }}>
            <div style={{ width: 26, height: 26, borderRadius: 6, background: 'var(--bl)', display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: '0 0 14px rgba(59,130,246,.4)', flexShrink: 0 }}>
              <Search size={12} color="#fff" />
            </div>
            <span style={{ fontFamily: 'var(--ui)', fontWeight: 700, fontSize: 14, color: 'var(--t1)', letterSpacing: '-.02em' }}>EzDocs</span>
            <code style={{ marginLeft: 'auto', fontSize: 8, padding: '2px 6px', borderRadius: 4, background: 'var(--bld)', color: '#93c5fd', border: '1px solid rgba(59,130,246,.2)', letterSpacing: '.07em' }}>BETA</code>
          </div>

          <div style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 9, borderBottom: '1px solid var(--ln)' }}>
            <div style={{ display: 'flex', gap: 2, background: 'rgba(0,0,0,.45)', borderRadius: 6, padding: 2 }}>
              {(['github', 'upload'] as const).map(m => (
                <button key={m} className={`ez-tab ${mode === m ? 'on' : 'off'}`} onClick={() => setMode(m)}>{m}</button>
              ))}
            </div>
            {mode === 'github' ? (
              <div style={{ position: 'relative' }}>
                <div style={{ position: 'absolute', left: 9, top: '50%', transform: 'translateY(-50%)', color: 'var(--t3)', pointerEvents: 'none' }}>
                  <GitBranch size={11} />
                </div>
                <input className="ez-inp" value={path} onChange={e => setPath(e.target.value)} onKeyDown={e => e.key === 'Enter' && analyze()} placeholder="github.com/owner/repo" />
              </div>
            ) : (
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: 10, color: 'var(--t2)', background: 'var(--c2)', border: '1px solid var(--ln)', borderRadius: 6, padding: '7px 10px', fontFamily: 'var(--ui)' }}>
                <UploadCloud size={13} />
                <span>{zipFileRef.current?.files?.[0]?.name ?? 'Choose .zip file…'}</span>
                <input ref={zipFileRef} type="file" accept=".zip" style={{ display: 'none' }} onChange={() => setPath(zipFileRef.current?.files?.[0]?.name ?? '')} />
              </label>
            )}
            <button className="ez-run" onClick={analyze} disabled={busy}>
              {busy ? <Activity size={13} className="ez-spin" /> : <Play size={12} fill="currentColor" />}
              {busy ? (busyMessage || (stats.n > 0 ? `${stats.n} nodes…` : 'Analyzing…')) : 'Run Analysis'}
            </button>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <button type="button" onClick={loadSavedAnalyses} disabled={busy}
                style={{ fontSize: 10, color: 'var(--t3)', background: 'none', border: '1px solid var(--ln)', borderRadius: 6, padding: '6px 10px', cursor: 'pointer', fontFamily: 'var(--ui)' }}>
                Load saved graph
              </button>
              {savedAnalyses.length > 0 && (
                <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 160, overflowY: 'auto' }}>
                  {savedAnalyses.slice(0, 15).map(a => (
                    <li key={a.codebase_id}>
                      <button type="button" onClick={() => loadGraphByCodebaseId(a.codebase_id)} disabled={busy}
                        style={{ width: '100%', textAlign: 'left', fontSize: 9, color: 'var(--t2)', background: 'var(--c2)', border: '1px solid var(--ln)', borderRadius: 4, padding: '5px 8px', cursor: 'pointer', fontFamily: 'var(--mono)' }}
                        title={a.source_path}>
                        {a.source_path.split(/[/\\]/).filter(Boolean).pop() || a.source_path || a.codebase_id.slice(0, 8)}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            {error && (
              <div style={{ display: 'flex', gap: 6, alignItems: 'flex-start', background: 'rgba(244,63,94,.08)', border: '1px solid rgba(244,63,94,.22)', borderRadius: 6, padding: '7px 9px', fontSize: 10, color: '#fda4af', lineHeight: 1.5 }}>
                <AlertCircle size={11} style={{ marginTop: 1, flexShrink: 0 }} />{error}
              </div>
            )}
          </div>

          <div className="ez-s" style={{ flex: 1, overflowY: 'auto', padding: '8px 5px' }} />

          <div style={{ borderTop: '1px solid var(--ln)', padding: '9px 13px', display: 'flex', alignItems: 'center' }}>
            {focusId && <button onClick={clearFocus} style={{ fontSize: 10, color: '#93c5fd', background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'var(--mono)', padding: 0 }}>← all nodes</button>}
            <span style={{ marginLeft: 'auto', fontSize: 9, color: 'var(--t3)', fontVariantNumeric: 'tabular-nums' }}>{stats.n > 0 ? `${stats.n}n · ${stats.e}e` : 'no graph'}</span>
          </div>
        </motion.div>

        {/* ── GRAPH ───────────────────────────────────────────────────── */}
        <div style={{ flex: 1, position: 'relative', minWidth: 0 }}>
          <MemberClickCtx.Provider value={onMemberClick}>
            {/* Dynamic by-file shading */}
            {(() => {
              const { x, y, zoom } = viewport;
              const PAD = 10;
              const toScreen = (fx: number, fy: number, fw: number, fh: number) => ({
                left: x + (fx - PAD) * zoom,
                top: y + (fy - PAD) * zoom,
                width: (fw + PAD * 2) * zoom,
                height: (fh + PAD * 2) * zoom,
              });
              if (clusters.length > 0) {
                return (
                  <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', zIndex: 0 }}>
                    {clusters.map((cluster) => {
                      const b = cluster.bounds;
                      const screen = toScreen(b.x, b.y, b.width, b.height);
                      const isFocused = focusId && nodes.find(n => n.id === focusId)?.data.cluster === cluster.filepath;
                      const isConnected = focusId && edges.some(e =>
                        (e.source === focusId && nodes.find(n => n.id === e.target)?.data.cluster === cluster.filepath) ||
                        (e.target === focusId && nodes.find(n => n.id === e.source)?.data.cluster === cluster.filepath)
                      );
                      const bg = isFocused
                        ? cluster.color.replace(/[\d.]+\)$/, '0.28)')
                        : isConnected
                          ? cluster.color.replace(/[\d.]+\)$/, '0.16)')
                          : cluster.color;
                      return (
                        <div key={cluster.id} style={{
                          position: 'absolute',
                          left: screen.left, top: screen.top,
                          width: screen.width, height: screen.height,
                          background: bg, borderRadius: 12, transition: 'all 0.2s ease',
                        }} />
                      );
                    })}
                  </div>
                );
              }
              return null;
            })()}

            <div style={{ position: 'absolute', inset: 0, zIndex: 1 }}>
              <ReactFlow
                nodes={nodes} edges={edges}
                onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
                nodeTypes={NODE_TYPES} edgeTypes={EDGE_TYPES}
                onNodeClick={onNodeClick} onNodeDoubleClick={onNodeDoubleClick} onPaneClick={onPaneClick}
                fitView fitViewOptions={{ padding: 0.12 }}
                minZoom={0.03} maxZoom={4}
                nodesFocusable={false} edgesFocusable={false} elementsSelectable={true} selectNodesOnDrag={false} nodesConnectable={false}
                onlyRenderVisibleElements={true} panOnDrag={true} panOnScroll={false}
                zoomOnScroll={true} zoomOnPinch={true} zoomOnDoubleClick={false}
                defaultEdgeOptions={{ type: 'ez', animated: false, focusable: false }}
                style={{ background: 'var(--c0)' }}
              >
                <Background gap={24} size={1} color="#1e293b" style={{ opacity: 0.4 }} variant={BackgroundVariant.Dots} />
                <CustomZoomControls />

                <Panel position="top-right">
                  {/* FIX: added top padding so panel doesn't overlap the floating VoiceSelector */}
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 10, paddingTop: 48 }}>
                    {nodes.length > 0 && (
                      <button onClick={startNarration} disabled={isNarrating}
                        style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, padding: '8px 14px', borderRadius: 8, border: '1px solid rgba(139,92,246,0.5)', background: isNarrating ? 'rgba(139,92,246,0.25)' : 'linear-gradient(135deg, rgba(139,92,246,0.18), rgba(167,139,250,0.18))', color: '#a78bfa', cursor: isNarrating ? 'not-allowed' : 'pointer', fontSize: 10, fontWeight: 700, letterSpacing: '.05em', fontFamily: 'var(--mono)', transition: 'all .15s', boxShadow: '0 2px 8px rgba(139,92,246,0.25)' }}
                        onMouseEnter={e => !isNarrating && (e.currentTarget.style.background = 'rgba(139,92,246,0.3)')}
                        onMouseLeave={e => !isNarrating && (e.currentTarget.style.background = 'linear-gradient(135deg, rgba(139,92,246,0.18), rgba(167,139,250,0.18))')}
                      >
                        {isNarrating ? <><Activity size={11} className="ez-spin" />NARRATING...</> : <><Volume2 size={11} />START TOUR</>}
                      </button>
                    )}
                  </div>
                </Panel>

                <Panel position="bottom-left">
                  <div style={{ background: 'rgba(11,16,23,.88)', backdropFilter: 'blur(8px)', border: '1px solid var(--ln)', borderRadius: 6, padding: '4px 9px', fontSize: 8.5, color: 'var(--t3)', letterSpacing: '.05em' }}>
                    scroll=zoom · drag=pan · click=focus
                  </div>
                </Panel>

                {focusId && (
                  <Panel position="top-center">
                    <motion.div initial={{ y: -12, opacity: 0 }} animate={{ y: 0, opacity: 1 }}
                      style={{ marginTop: 8, background: 'rgba(11,16,23,.92)', backdropFilter: 'blur(10px)', border: '1px solid var(--ln2)', borderRadius: 10, padding: '7px 16px', display: 'flex', alignItems: 'center', gap: 10, boxShadow: '0 8px 28px rgba(0,0,0,.5)' }}>
                      <Activity size={10} style={{ color: 'var(--bl)' }} />
                      <span style={{ fontSize: 11, color: 'var(--t2)' }}>Focus: <b style={{ color: 'var(--t1)', fontFamily: 'var(--mono)' }}>{selectedNode?.data.label}</b></span>
                      <button onClick={clearFocus} style={{ fontSize: 9.5, padding: '2px 9px', borderRadius: 4, border: '1px solid var(--ln2)', background: 'var(--bld)', color: '#93c5fd', cursor: 'pointer', fontFamily: 'var(--mono)' }}>× reset</button>
                    </motion.div>
                  </Panel>
                )}

                {nodes.length === 0 && !busy && (
                  <Panel position="top-center">
                    <div style={{ marginTop: '17vh', textAlign: 'center', userSelect: 'none' }}>
                      <div style={{ fontSize: 48, opacity: 0.04, marginBottom: 14 }}>⬡</div>
                      <div style={{ fontSize: 13, color: 'var(--t3)', fontFamily: 'var(--ui)', fontWeight: 600 }}>No graph loaded</div>
                      <div style={{ fontSize: 10, color: 'var(--t4)', marginTop: 5 }}>Enter a path → Run Analysis</div>
                    </div>
                  </Panel>
                )}

                {busy && nodes.length === 0 && (
                  <Panel position="top-center">
                    <div className="ez-loader-wrap" style={{ marginTop: '14vh' }}>
                      <div
                        className="ez-loader-space"
                        onMouseMove={onLoaderMouseMove}
                        onMouseLeave={resetLoaderPointer}
                        style={{
                          ['--mouse-x' as any]: `${loaderPointer.x}`,
                          ['--mouse-y' as any]: `${loaderPointer.y}`,
                        }}
                      >
                        <div className="ez-loader-stars" aria-hidden="true" />
                        <div className="ez-loader-orbit" aria-hidden="true">
                          <div className="ez-loader-rocket">
                            <div className="ez-loader-rocket-body" />
                            <div className="ez-loader-rocket-window" />
                            <div className="ez-loader-rocket-fin ez-loader-rocket-fin-left" />
                            <div className="ez-loader-rocket-fin ez-loader-rocket-fin-right" />
                            <div className="ez-loader-rocket-flame" />
                          </div>
                        </div>
                        <div className="ez-loader-earth" aria-hidden="true">
                          <div className="ez-loader-earth-core">
                            <span className="ez-loader-continent ez-loader-continent-a" />
                            <span className="ez-loader-continent ez-loader-continent-b" />
                            <span className="ez-loader-continent ez-loader-continent-c" />
                          </div>
                        </div>
                      </div>
                      <div className="ez-loader-dots" aria-hidden="true"><span /><span /><span /></div>
                      <div style={{ fontSize: 12, color: 'var(--t2)', fontFamily: 'var(--ui)', fontWeight: 600, marginTop: 10 }}>
                        Earth is coordinating launch sequence...
                      </div>
                      <div style={{ fontSize: 10, color: 'var(--t3)', fontFamily: 'var(--mono)', marginTop: 5 }}>
                        {busyMessage || (stats.n > 0 ? `Parsed ${stats.n} nodes so far` : 'Scanning files in batches...')}
                      </div>
                    </div>
                  </Panel>
                )}
              </ReactFlow>
            </div>

            {/* Cluster labels */}
            {clusters.length > 0 && (() => {
              const { x, y, zoom } = viewport;
              return (
                <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', zIndex: 2 }}>
                  {clusters.map(cluster => {
                    const isFocused = focusId && nodes.find(n => n.id === focusId)?.data.cluster === cluster.filepath;
                    const isConnected = focusId && edges.some(e =>
                      (e.source === focusId && nodes.find(n => n.id === e.target)?.data.cluster === cluster.filepath) ||
                      (e.target === focusId && nodes.find(n => n.id === e.source)?.data.cluster === cluster.filepath)
                    );
                    const labelLeft = x + (cluster.bounds.x + 8) * zoom;
                    const labelTop = y + (cluster.bounds.y - 22) * zoom;
                    return (
                      <div key={cluster.id} style={{
                        position: 'absolute',
                        left: labelLeft, top: labelTop,
                        fontSize: 10, fontWeight: 600,
                        color: isFocused ? '#93c5fd' : isConnected ? 'var(--t2)' : 'var(--t3)',
                        fontFamily: 'var(--mono)', letterSpacing: '.05em',
                        opacity: isFocused || isConnected ? 1 : 0.6,
                        transition: 'all 0.2s ease',
                        textShadow: '0 1px 2px rgba(0,0,0,0.5)',
                      }}>
                        📁 {cluster.filepath.split('/').pop()?.split('\\').pop() || cluster.filepath}
                        {cluster.depth === 0 && <span style={{ marginLeft: 5, fontSize: 8, padding: '1px 4px', borderRadius: 3, background: 'rgba(20,184,166,0.2)', border: '1px solid rgba(20,184,166,0.4)', color: 'var(--tl)', verticalAlign: 'middle' }}>ENTRY</span>}
                        {(cluster.depth ?? 0) > 0 && <span style={{ marginLeft: 5, fontSize: 8, padding: '1px 4px', borderRadius: 3, background: 'rgba(0,0,0,0.3)', color: 'var(--t4)', verticalAlign: 'middle' }}>d{cluster.depth}</span>}
                      </div>
                    );
                  })}
                </div>
              );
            })()}
          </MemberClickCtx.Provider>
        </div>

        {/* ── DETAIL PANEL ────────────────────────────────────────────── */}
        <AnimatePresence>
          {toasts.map((toast, index) => (
            <motion.div
              key={toast.id}
              className="ez-toast"
              initial={{ opacity: 0, x: 40, y: 12 }}
              animate={{ opacity: 1, x: 0, y: 0 }}
              exit={{ opacity: 0, x: 28, y: 8 }}
              transition={{ duration: 0.22 }}
              style={{ right: 18, bottom: 18 + index * 78 }}
            >
              <div className="ez-toast-title">Backend Notice</div>
              <div className="ez-toast-body">{toast.message}</div>
              <div className="ez-toast-timeline" />
            </motion.div>
          ))}
          {selectedNode && (
            <motion.div key="dp" initial={{ x: 420, opacity: 0 }} animate={{ x: 0, opacity: 1 }} exit={{ x: 420, opacity: 0 }} transition={{ type: 'spring', damping: 32, stiffness: 280 }}
              style={{ width: 420, borderLeft: '1px solid var(--ln)', background: 'var(--c1)', display: 'flex', flexDirection: 'column', position: 'absolute', right: 0, top: 0, bottom: 0, zIndex: 40, boxShadow: '-20px 0 60px rgba(0,0,0,.65)' }}>

              <div style={{ height: 50, padding: '0 14px', borderBottom: '1px solid var(--ln)', display: 'flex', alignItems: 'center', gap: 9, flexShrink: 0 }}>
                <div style={{ padding: '4px 5px', borderRadius: 5, background: selectedNode.data.type === 'class' ? 'rgba(245,158,11,.1)' : 'var(--bld)', border: `1px solid ${selectedNode.data.type === 'class' ? 'rgba(245,158,11,.25)' : 'rgba(59,130,246,.25)'}` }}>
                  {selectedNode.data.type === 'class' ? <Layers size={11} style={{ color: 'var(--am)' }} /> : <Terminal size={11} style={{ color: 'var(--bl)' }} />}
                </div>
                <div style={{ flex: 1, overflow: 'hidden' }}>
                  <div style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--t1)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{selectedNode.data.label}</div>
                  <div style={{ fontSize: 8.5, color: 'var(--t3)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{selectedNode.data.filepath}&nbsp;·&nbsp;L{selectedNode.data.start_line}–{selectedNode.data.end_line}</div>
                </div>
                <button onClick={() => { setSelectedNode(null); clearFocus(); }} style={{ background: 'none', border: 'none', color: 'var(--t3)', cursor: 'pointer', padding: 4, borderRadius: 4, lineHeight: 0 }} onMouseEnter={e => (e.currentTarget.style.color = 'var(--t1)')} onMouseLeave={e => (e.currentTarget.style.color = 'var(--t3)')}>
                  <X size={14} />
                </button>
              </div>

              <div className="ez-s" style={{ flex: 1, overflowY: 'auto' }}>
                <SyntaxHighlighter language={syLang(selectedNode.data.filepath)} style={oneDark} customStyle={{ margin: 0, padding: '16px 18px', fontSize: 10, lineHeight: 1.75, background: 'transparent', fontFamily: 'var(--mono)' }} showLineNumbers lineNumberStyle={{ color: 'var(--t4)', minWidth: 26 }} startingLineNumber={selectedNode.data.start_line || 1}>
                  {selectedNode.data.code || '// no source'}
                </SyntaxHighlighter>

                <div style={{ borderTop: '1px solid var(--ln)', padding: '14px 16px', background: 'rgba(0,0,0,.22)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 9.5, fontWeight: 700, color: 'var(--t2)', letterSpacing: '.08em', textTransform: 'uppercase' }}>
                      <Cpu size={10} style={{ color: 'var(--bl)' }} />AI Analysis
                    </div>
                    <div style={{ display: 'flex', gap: 6 }}>
                      {(selectedNode.data.hasHidden || (selectedNode.type === 'fileGroup' && selectedNode.data.hasAnyHidden)) && (
                        <button
                          onClick={() => {
                            if (selectedNode.type === 'fileGroup' && selectedNode.data.hasAnyHidden) {
                              const ids = (selectedNode.data.members as { id: string; hasHidden?: boolean }[]).filter(m => m.hasHidden).map(m => m.id);
                              expandFromNodes(ids);
                            } else {
                              expandNode(selectedNode.id);
                            }
                          }}
                          style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 9.5, padding: '3px 9px', borderRadius: 5, border: '1px solid rgba(20,184,166,0.5)', background: 'rgba(20,184,166,0.1)', color: '#5eead4', cursor: 'pointer', fontWeight: 600, fontFamily: 'var(--mono)' }}
                          onMouseEnter={e => (e.currentTarget.style.background = 'rgba(20,184,166,0.2)')}
                          onMouseLeave={e => (e.currentTarget.style.background = 'rgba(20,184,166,0.1)')}
                        >
                          <Plus size={10} />Expand Nodes
                        </button>
                      )}
                      {explanation && !aiStreaming && (
                        <button onClick={toggleSpeech} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 9.5, padding: '3px 9px', borderRadius: 5, border: '1px solid var(--ln2)', background: isSpeaking ? 'var(--bld)' : 'transparent', color: isSpeaking ? '#93c5fd' : 'var(--t2)', cursor: 'pointer', fontWeight: 600, fontFamily: 'var(--mono)' }} onMouseEnter={e => (e.currentTarget.style.color = '#93c5fd')} onMouseLeave={e => (e.currentTarget.style.color = isSpeaking ? '#93c5fd' : 'var(--t2)')}>
                          <Volume2 size={10} style={{ opacity: isSpeaking ? 1 : 0.7 }} />{isSpeaking ? 'Stop' : 'Narrate'}
                        </button>
                      )}
                      {!explanation && !aiStreaming && (
                        <button onClick={explain} style={{ fontSize: 9.5, padding: '3px 11px', borderRadius: 5, border: '1px solid var(--ln2)', background: 'var(--bld)', color: '#93c5fd', cursor: 'pointer', fontWeight: 600, fontFamily: 'var(--mono)' }} onMouseEnter={e => (e.currentTarget.style.background = 'var(--blg)')} onMouseLeave={e => (e.currentTarget.style.background = 'var(--bld)')}>
                          Generate
                        </button>
                      )}
                    </div>
                    {aiStreaming && <span style={{ fontSize: 9, color: 'var(--t3)', display: 'flex', alignItems: 'center', gap: 4 }}><Activity size={9} className="ez-spin" />Streaming…</span>}
                  </div>
                  {explanation ? (
                    <div style={{ fontSize: 11, color: 'var(--t2)', lineHeight: 1.85 }}><ReactMarkdown>{explanation}</ReactMarkdown></div>
                  ) : (
                    <div style={{ height: 80, display: 'flex', alignItems: 'center', justifyContent: 'center', border: '1px dashed var(--ln2)', borderRadius: 6 }}>
                      <span style={{ fontSize: 10, color: 'var(--t3)' }}>{aiStreaming ? 'Generating…' : 'Click Generate to explain'}</span>
                    </div>
                  )}
                </div>

                <div style={{ padding: '12px 16px', borderTop: '1px solid var(--ln)', background: 'rgba(0,0,0,.15)' }}>
                  <button onClick={startNodeNarration} disabled={isNodeNarrating}
                    style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 7, padding: '9px 14px', borderRadius: 8, border: '1px solid rgba(139,92,246,0.4)', background: isNodeNarrating ? 'rgba(139,92,246,0.2)' : 'linear-gradient(135deg, rgba(139,92,246,0.12), rgba(167,139,250,0.12))', color: '#a78bfa', cursor: isNodeNarrating ? 'not-allowed' : 'pointer', fontSize: 10, fontWeight: 700, letterSpacing: '.06em', fontFamily: 'var(--mono)', transition: 'all .15s', boxShadow: '0 2px 10px rgba(139,92,246,0.15)' }}
                    onMouseEnter={e => !isNodeNarrating && (e.currentTarget.style.background = 'rgba(139,92,246,0.22)')}
                    onMouseLeave={e => !isNodeNarrating && (e.currentTarget.style.background = 'linear-gradient(135deg, rgba(139,92,246,0.12), rgba(167,139,250,0.12))')}
                  >
                    {isNodeNarrating ? <><Activity size={11} className="ez-spin" />DEEP-DIVING...</> : <><Microscope size={11} />EXPLAIN THIS NODE</>}
                  </button>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* ── CODEBASE TOUR NARRATOR PANEL ─────────────────────────────── */}
        <AnimatePresence>
          {showNarrator && (
            <motion.div key="narrator" initial={{ y: 600, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: 600, opacity: 0 }} transition={{ type: 'spring', damping: 28, stiffness: 260 }}
              style={{ position: 'absolute', left: 20, right: selectedNode ? 440 : 20, bottom: 20, maxHeight: '40vh', minHeight: 200, background: 'linear-gradient(145deg, rgba(17,23,32,0.95), rgba(11,16,23,0.98))', backdropFilter: 'blur(16px)', WebkitBackdropFilter: 'blur(16px)', border: '1px solid rgba(139,92,246,0.4)', borderRadius: 12, padding: '16px 20px', boxShadow: '0 -10px 60px rgba(139,92,246,0.2)', display: 'flex', flexDirection: 'column', zIndex: 30, overflow: 'hidden' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12, paddingBottom: 10, borderBottom: '1px solid rgba(139,92,246,0.25)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <div style={{ width: 28, height: 28, borderRadius: 6, background: 'linear-gradient(135deg, rgba(139,92,246,0.25), rgba(167,139,250,0.25))', border: '1px solid rgba(139,92,246,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Volume2 size={14} style={{ color: '#a78bfa' }} /></div>
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: '#a78bfa' }}>AI Narrator</div>
                    <div style={{ fontSize: 9, color: 'var(--t3)', marginTop: 1 }}>{isNarrating ? 'Exploring codebase...' : 'Tour complete'}</div>
                  </div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <button onClick={toggleNarratorSpeech} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 9.5, padding: '4px 9px', borderRadius: 5, border: `1px solid ${narratorSpeechOn ? 'rgba(139,92,246,0.5)' : 'var(--ln2)'}`, background: narratorSpeechOn ? 'rgba(139,92,246,0.15)' : 'transparent', color: narratorSpeechOn ? '#a78bfa' : 'var(--t3)', cursor: 'pointer', fontWeight: 600, fontFamily: 'var(--mono)' }}>
                    {narratorSpeechOn ? <Volume2 size={11} /> : <VolumeX size={11} />}{narratorSpeechOn ? 'Voice On' : 'Voice Off'}
                  </button>
                  <button onClick={() => { setShowNarrator(false); stopNarratorSpeech(); }} style={{ background: 'none', border: 'none', color: 'var(--t3)', cursor: 'pointer', padding: 4, borderRadius: 4, lineHeight: 0 }} onMouseEnter={e => (e.currentTarget.style.color = '#a78bfa')} onMouseLeave={e => (e.currentTarget.style.color = 'var(--t3)')}>
                    <X size={16} />
                  </button>
                </div>
              </div>
              <div className="ez-s" style={{ flex: 1, overflowY: 'auto', fontSize: 11.5, lineHeight: 1.8, color: 'var(--t2)', paddingRight: 8 }}>
                {narration ? <ReactMarkdown>{narration}</ReactMarkdown> : (
                  <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--t3)', fontStyle: 'italic' }}>
                    {isNarrating ? <div style={{ textAlign: 'center' }}><Activity size={20} className="ez-spin" style={{ color: '#a78bfa', marginBottom: 10 }} /><div>Starting narration...</div></div> : 'Click "Start Tour" to begin'}
                  </div>
                )}
              </div>
              {narration && !isNarrating && (
                <div style={{ marginTop: 12, paddingTop: 10, borderTop: '1px solid rgba(139,92,246,0.25)', display: 'flex', justifyContent: 'flex-end' }}>
                  <button onClick={() => { setNarration(''); startNarration(); }} style={{ fontSize: 10, padding: '5px 12px', borderRadius: 6, border: '1px solid rgba(139,92,246,0.4)', background: 'rgba(139,92,246,0.12)', color: '#a78bfa', cursor: 'pointer', fontWeight: 600, fontFamily: 'var(--mono)' }} onMouseEnter={e => (e.currentTarget.style.background = 'rgba(139,92,246,0.22)')} onMouseLeave={e => (e.currentTarget.style.background = 'rgba(139,92,246,0.12)')}>🔄 Restart Tour</button>
                </div>
              )}
            </motion.div>
          )}
        </AnimatePresence>

        {/* ── PER-NODE NARRATOR PANEL ───────────────────────────────────── */}
        <AnimatePresence>
          {showNodeNarrator && (
            <motion.div key="node-narrator" initial={{ y: 600, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: 600, opacity: 0 }} transition={{ type: 'spring', damping: 28, stiffness: 260 }}
              style={{ position: 'absolute', left: 20, right: selectedNode ? 440 : 20, bottom: showNarrator ? 'calc(40vh + 30px)' : 20, maxHeight: '40vh', minHeight: 200, background: 'linear-gradient(145deg, rgba(17,14,32,0.96), rgba(11,8,23,0.98))', backdropFilter: 'blur(16px)', WebkitBackdropFilter: 'blur(16px)', border: '1px solid rgba(139,92,246,0.35)', borderRadius: 12, padding: '16px 20px', boxShadow: '0 -10px 60px rgba(139,92,246,0.18)', display: 'flex', flexDirection: 'column', zIndex: 31, overflow: 'hidden' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12, paddingBottom: 10, borderBottom: '1px solid rgba(139,92,246,0.2)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <div style={{ width: 28, height: 28, borderRadius: 6, background: 'linear-gradient(135deg, rgba(139,92,246,0.2), rgba(167,139,250,0.2))', border: '1px solid rgba(139,92,246,0.4)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Microscope size={14} style={{ color: '#a78bfa' }} /></div>
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: '#a78bfa' }}>Node Deep-Dive</div>
                    <div style={{ fontSize: 9, color: 'var(--t3)', marginTop: 1 }}>{isNodeNarrating ? `Analyzing ${selectedNode?.data.label ?? 'node'}...` : `${selectedNode?.data.label ?? 'node'} · complete`}</div>
                  </div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <button onClick={toggleNodeNarratorSpeech} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 9.5, padding: '4px 9px', borderRadius: 5, border: `1px solid ${nodeNarratorSpeechOn ? 'rgba(139,92,246,0.4)' : 'var(--ln2)'}`, background: nodeNarratorSpeechOn ? 'rgba(139,92,246,0.12)' : 'transparent', color: nodeNarratorSpeechOn ? '#a78bfa' : 'var(--t3)', cursor: 'pointer', fontWeight: 600, fontFamily: 'var(--mono)' }}>
                    {nodeNarratorSpeechOn ? <Volume2 size={11} /> : <VolumeX size={11} />}{nodeNarratorSpeechOn ? 'Voice On' : 'Voice Off'}
                  </button>
                  <button onClick={() => { setShowNodeNarrator(false); stopNodeNarratorSpeech(); }} style={{ background: 'none', border: 'none', color: 'var(--t3)', cursor: 'pointer', padding: 4, borderRadius: 4, lineHeight: 0 }} onMouseEnter={e => (e.currentTarget.style.color = '#a78bfa')} onMouseLeave={e => (e.currentTarget.style.color = 'var(--t3)')}>
                    <X size={16} />
                  </button>
                </div>
              </div>
              <div className="ez-s" style={{ flex: 1, overflowY: 'auto', fontSize: 11.5, lineHeight: 1.8, color: 'var(--t2)', paddingRight: 8 }}>
                {nodeNarration ? <ReactMarkdown>{nodeNarration}</ReactMarkdown> : (
                  <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--t3)', fontStyle: 'italic' }}>
                    {isNodeNarrating ? <div style={{ textAlign: 'center' }}><Activity size={20} className="ez-spin" style={{ color: '#a78bfa', marginBottom: 10 }} /><div>Analyzing node...</div></div> : 'Click "Explain This Node" in the detail panel'}
                  </div>
                )}
              </div>
              {nodeNarration && !isNodeNarrating && (
                <div style={{ marginTop: 12, paddingTop: 10, borderTop: '1px solid rgba(139,92,246,0.2)', display: 'flex', justifyContent: 'flex-end' }}>
                  <button onClick={() => { setNodeNarration(''); startNodeNarration(); }} style={{ fontSize: 10, padding: '5px 12px', borderRadius: 6, border: '1px solid rgba(139,92,246,0.3)', background: 'rgba(139,92,246,0.1)', color: '#a78bfa', cursor: 'pointer', fontWeight: 600, fontFamily: 'var(--mono)' }} onMouseEnter={e => (e.currentTarget.style.background = 'rgba(139,92,246,0.2)')} onMouseLeave={e => (e.currentTarget.style.background = 'rgba(139,92,246,0.1)')}>🔄 Re-analyze</button>
                </div>
              )}
            </motion.div>
          )}
        </AnimatePresence>

        <PromptBar
          onSubmit={(query) => {
            console.log('prompt:', query);
          }}
          loading={aiStreaming}
        />
      </div>
    </FocusCtx.Provider>
  );
}

function CustomZoomControls() {
  const { zoomIn, zoomOut, zoomTo } = useReactFlow();
  const { zoom } = useViewport();
  return (
    <Panel position="bottom-right" style={{ marginBottom: 20, marginRight: 20 }}>
      <div style={{ background: 'rgba(11,16,23,.88)', backdropFilter: 'blur(8px)', border: '1px solid var(--ln2)', borderRadius: 8, padding: '6px 12px', display: 'flex', alignItems: 'center', gap: 10, boxShadow: '0 8px 32px rgba(0,0,0,.6)' }}>
        <button onClick={() => zoomOut({ duration: 200 })} style={{ background: 'transparent', border: 'none', color: 'var(--t2)', cursor: 'pointer', fontSize: 16, lineHeight: 1 }}>-</button>
        <input type="range" min={0.03} max={4} step={0.01} value={zoom} onChange={e => zoomTo(parseFloat(e.target.value), { duration: 150 })} className="ez-zoom-slider" />
        <button onClick={() => zoomIn({ duration: 200 })} style={{ background: 'transparent', border: 'none', color: 'var(--t2)', cursor: 'pointer', fontSize: 16, lineHeight: 1 }}>+</button>
      </div>
    </Panel>
  );
}

export default function EzDocsIDE() {
  return (
    <ReactFlowProvider>
      <EzDocsInner />
    </ReactFlowProvider>
  );
}