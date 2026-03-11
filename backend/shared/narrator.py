"""
EzDocs AI Narrator

Streams a guided codebase tour over WebSocket using two message types:
  {"type": "focus",  "node_id": str, "label": str, "filepath": str}
  {"type": "text",   "chunk": str}

The frontend uses focus frames to highlight the current node in the graph.
Ordering: degree-weighted (orchestrators first), then BFS cross-file deps.
"""

import json
import logging
from fastapi import WebSocket, WebSocketDisconnect
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError

from shared import ai

log = logging.getLogger("ezdocs")

# Any exception that means "the socket is gone — stop trying to send"
_CLOSED = (WebSocketDisconnect, ConnectionClosedOK, ConnectionClosedError)

_NARRATOR_SYSTEM = (
    "You are a senior engineer giving a codebase walkthrough. "
    "Be concise. Do not mention callers, callees, or metadata — only what the code does."
)


def _compute_degrees(nodes: list[dict], edges: list[dict]) -> tuple[dict[str, int], dict[str, int]]:
    indegree:  dict[str, int] = {n.get("id"): 0 for n in nodes}
    outdegree: dict[str, int] = {n.get("id"): 0 for n in nodes}
    for edge in edges:
        src = edge.get("source")
        tgt = edge.get("target")
        if src in outdegree: outdegree[src] += 1
        if tgt in indegree:  indegree[tgt]  += 1
    return indegree, outdegree


def _find_entry_node(nodes: list[dict], indegree: dict, outdegree: dict) -> dict:
    flagged = next((n for n in nodes if n.get("data", {}).get("isEntry")), None)
    if flagged:
        return flagged

    def score(n: dict) -> float:
        nid = n.get("id")
        label = n.get("data", {}).get("label", "").lower()
        name_bonus = 10 if label in {"main", "app", "run", "index", "start", "cli", "server", "init"} else 0
        return outdegree.get(nid, 0) - indegree.get(nid, 0) + name_bonus

    return max(nodes, key=score)


def _order_entry_file_nodes(nodes, filepath, indegree, outdegree):
    file_nodes = [
        n for n in nodes
        if n.get("data", {}).get("filepath") == filepath
        and n.get("data", {}).get("code", "").strip()
    ]
    return sorted(
        file_nodes,
        key=lambda n: (
            -outdegree.get(n.get("id"), 0),
             indegree.get(n.get("id"), 0),
             n.get("data", {}).get("start_line", 0),
        ),
    )


async def _send_focus(ws: WebSocket, node: dict) -> bool:
    """Send a focus frame. Returns False if the socket is already closed."""
    try:
        data = node.get("data", {})
        await ws.send_text(json.dumps({
            "type":     "focus",
            "node_id":  node.get("id", ""),
            "label":    data.get("label", ""),
            "filepath": data.get("filepath", ""),
        }))
        return True
    except _CLOSED:
        return False


async def _send_text(ws: WebSocket, chunk: str) -> bool:
    """Send a text chunk. Returns False if the socket is already closed."""
    try:
        await ws.send_text(json.dumps({"type": "text", "chunk": chunk}))
        return True
    except _CLOSED:
        return False


async def _stream_explanation(ws: WebSocket, messages: list[dict], label: str) -> bool:
    """
    Stream an HF chat response to the websocket chunk by chunk.
    Returns False if the socket closed mid-stream, True on success.
    Falls back to a graceful error message if HF fails.
    """
    try:
        async for chunk in ai.chat_stream(messages):
            if chunk:
                if not await _send_text(ws, chunk):
                    return False
        return True
    except _CLOSED:
        return False
    except ai.AIProviderError as exc:
        log.warning("Narration AI unavailable for %s: %s", label, exc)
        return await _send_text(
            ws,
            "AI backend is unavailable for live narration. "
            "Configure OLLAMA_HOST or HF_TOKEN, then retry.\n\n",
        )
    except Exception as exc:
        log.warning("Narration failed for %s: %s", label, exc)
        return await _send_text(ws, "Unable to generate narration for this component right now.\n\n")


async def run_narration(websocket: WebSocket, graph_cache: dict) -> None:
    try:
        if not graph_cache:
            await _send_text(websocket, "❌ Error: no graph loaded yet. Analyze a codebase first.")
            return

        nodes: list[dict] = graph_cache.get("nodes", [])
        edges: list[dict] = graph_cache.get("edges", [])

        if not nodes:
            await _send_text(websocket, "❌ Error: no nodes in graph.")
            return

        indegree, outdegree = _compute_degrees(nodes, edges)

        node_map = {n.get("id"): n for n in nodes}
        adj_map: dict[str, list[str]] = {}
        for edge in edges:
            src = edge.get("source")
            tgt = edge.get("target")
            if src not in adj_map:
                adj_map[src] = []
            adj_map[src].append(tgt)

        entry_node     = _find_entry_node(nodes, indegree, outdegree)
        entry_filepath = entry_node.get("data", {}).get("filepath", "")
        entry_out      = outdegree.get(entry_node.get("id"), 0)
        entry_in       = indegree.get(entry_node.get("id"), 0)

        log.info(
            "WS narration started — entry=%s (out=%d, in=%d), nodes=%d",
            entry_node.get("data", {}).get("label"), entry_out, entry_in, len(nodes),
        )

        same_file_nodes = _order_entry_file_nodes(nodes, entry_filepath, indegree, outdegree)

        visited_ids = {n.get("id") for n in same_file_nodes}
        bfs_queue   = list(visited_ids)
        cross_file_nodes: list[dict] = []

        while bfs_queue:
            current_id = bfs_queue.pop(0)
            children = sorted(adj_map.get(current_id, []), key=lambda cid: -outdegree.get(cid, 0))
            for child_id in children:
                if child_id in visited_ids:
                    continue
                child_node = node_map.get(child_id)
                if not child_node or not child_node.get("data", {}).get("code", "").strip():
                    continue
                visited_ids.add(child_id)
                cross_file_nodes.append(child_node)
                bfs_queue.append(child_id)

        tour_nodes = same_file_nodes + cross_file_nodes[:20]

        if not await _send_text(websocket, "# Codebase Tour\n\n"): return
        if not await _send_text(websocket, f"Starting from **`{entry_filepath}`**\n\n"): return
        if not await _send_text(websocket, f"*Explaining {len(tour_nodes)} components*\n\n"): return
        if not await _send_text(websocket, "---\n\n"): return

        current_file    = ""
        total_explained = 0

        for idx, node in enumerate(tour_nodes, 1):
            node_data  = node.get("data", {})
            label      = node_data.get("label", "Unknown")
            code       = node_data.get("code", "").strip()
            node_type  = node_data.get("type", "function")
            filepath   = node_data.get("filepath", "")

            if not code:
                continue

            if not await _send_focus(websocket, node): return

            if filepath != current_file:
                current_file = filepath
                section = "Current File" if filepath == entry_filepath else "Dependency"
                if not await _send_text(websocket, f"\n## {section}: `{filepath}`\n\n"): return

            if not await _send_text(websocket, f"\n### {idx}. `{label}` ({node_type})\n\n"): return

            messages = [
                {
                    "role": "system",
                    "content": _NARRATOR_SYSTEM,
                },
                {
                    "role": "user",
                    "content": (
                        f"Explain this {node_type} concisely. "
                        f"Use only the function name and its definition below.\n\n"
                        f"**Name:** `{label}`\n\n"
                        f"**Definition:**\n```\n{code[:1200]}\n```\n\n"
                        f"In 2-3 sentences: what it does and key logic. Be direct and technical. No fluff."
                    ),
                },
            ]

            ok = await _stream_explanation(websocket, messages, label)
            if not ok:
                return
            if not await _send_text(websocket, "\n\n"):
                return
            total_explained += 1

        if not await _send_text(websocket, "\n---\n\n"): return
        if not await _send_text(websocket, "## Tour Complete\n\n"): return
        if not await _send_text(websocket, f"Explained **{total_explained} components** in orchestrator order.\n\n"): return
        await _send_text(websocket, "Click on any node in the graph to explore further!")

    except _CLOSED:
        log.info("WS client disconnected during narration")
    except Exception as exc:
        log.exception("WS narration error")
        await _send_text(websocket, f"\n\n❌ Error: {exc}")


# ─── Per-Node Narration ───────────────────────────────────────────────────────

async def run_node_narration(websocket: WebSocket, graph_cache: dict, node_id: str) -> None:
    """
    Stream a focused narration for a single node plus its direct callers/callees.

    Protocol (same as run_narration):
      {"type": "focus", "node_id": str, "label": str, "filepath": str}
      {"type": "text",  "chunk": str}
    """
    try:
        if not graph_cache:
            await _send_text(websocket, "No graph loaded. Analyze a codebase first.")
            return

        nodes: list[dict] = graph_cache.get("nodes", [])
        edges: list[dict] = graph_cache.get("edges", [])

        node_map = {n.get("id"): n for n in nodes}
        target_node = node_map.get(node_id)

        if not target_node:
            await _send_text(websocket, f"Node `{node_id}` not found in graph.")
            return

        indegree, outdegree = _compute_degrees(nodes, edges)

        callees: list[dict] = []
        callers: list[dict] = []

        for edge in edges:
            src, tgt = edge.get("source"), edge.get("target")
            if src == node_id and tgt in node_map:
                callees.append(node_map[tgt])
            if tgt == node_id and src in node_map:
                callers.append(node_map[src])

        callees = sorted(callees, key=lambda n: -outdegree.get(n.get("id"), 0))[:5]
        callers = sorted(callers, key=lambda n: -outdegree.get(n.get("id"), 0))[:5]

        node_data  = target_node.get("data", {})
        label      = node_data.get("label", "Unknown")
        code       = node_data.get("code", "").strip()
        node_type  = node_data.get("type", "function")
        filepath   = node_data.get("filepath", "")
        start_line = node_data.get("start_line", 0)
        end_line   = node_data.get("end_line", 0)

        log.info(
            "WS node narration: %s (%s) — %d callers, %d callees",
            label, node_id, len(callers), len(callees),
        )

        if not await _send_focus(websocket, target_node): return

        if not await _send_text(websocket, f"# `{label}`\n\n"): return
        if not await _send_text(websocket, f"**File:** `{filepath}` · Lines {start_line}-{end_line}\n\n"): return
        if not await _send_text(websocket, "---\n\n"): return

        messages = [
            {
                "role": "system",
                "content": "You are a senior engineer. Explain only from the code. Do not list callers or callees.",
            },
            {
                "role": "user",
                "content": (
                    f"Explain this {node_type} using only its name and definition. "
                    f"Do not mention callers, callees, or other metadata.\n\n"
                    f"**Name:** `{label}`\n\n"
                    f"**Definition:**\n```\n{code[:1200]}\n```\n\n"
                    f"In 3-5 sentences: what it does, key logic, and how it works. Be direct and technical."
                ),
            },
        ]

        if not await _send_text(websocket, f"## What `{label}` Does\n\n"): return

        ok = await _stream_explanation(websocket, messages, label)
        if not ok:
            return

        if not await _send_text(websocket, "\n\n"): return
        if not await _send_text(websocket, "---\n\n"): return
        await _send_text(websocket, "Click another node and hit **Explain This Node** to explore it!")

    except _CLOSED:
        log.info("WS client disconnected during node narration")
    except Exception as exc:
        log.exception("WS node narration error")
        await _send_text(websocket, f"\n\n❌ Error: {exc}")