"""
LangGraph interactive narrator — state-machine-based codebase tour.

Graph topology:
  plan_tour → explain_node → pause (interrupt) → route_input
                   ↑                                   |
                   |            [continue]             |
              advance ←──────────────────────────────── ┤
                                                        | [question]
                                          answer_question → advance
                                                        | [focus]
                                          change_focus ─→ explain_node

Frontend WebSocket protocol
  Backend → Frontend:
    {"type": "focus", "node_id": str, "label": str, "filepath": str}
    {"type": "text",  "chunk": str}
    {"type": "wait",  "message": str}   ← new: pause for user input
    {"type": "complete"}                ← new: tour finished
    {"type": "error",  "message": str}

  Frontend → Backend (only during "wait"):
    {"type": "continue"}
    {"type": "question", "text": str}
    {"type": "focus",    "node_id": str}
"""

import asyncio
import json
import logging
from typing import Any, Optional
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect
from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.runnables import RunnableConfig
from langchain_ollama import ChatOllama
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt
from typing_extensions import TypedDict
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from shared.config import OLLAMA_HOST, OLLAMA_MODEL
from shared.narrator import (
    _compute_degrees,
    _find_entry_node,
    _order_entry_file_nodes,
)

log = logging.getLogger("xplore.narrator_graph")

_CLOSED = (WebSocketDisconnect, ConnectionClosedOK, ConnectionClosedError)

# ─── State ────────────────────────────────────────────────────────────────────


class NarrationState(TypedDict):
    nodes: list[dict]
    edges: list[dict]
    tour_nodes: list[dict]       # ordered walk order, built by plan_tour
    current_index: int           # position in tour_nodes
    user_message: Optional[dict] # last user message from interrupt resume


# ─── LLM factory ──────────────────────────────────────────────────────────────

_llm_instance: "ChatOllama | None" = None


def _llm() -> ChatOllama:
    global _llm_instance
    if _llm_instance is None:
        _llm_instance = ChatOllama(model=OLLAMA_MODEL, base_url=OLLAMA_HOST, streaming=True)
    return _llm_instance


# ─── Helper: dispatch WS event (captured by astream_events outer loop) ────────


async def _dispatch(event: dict, config: RunnableConfig) -> None:
    await adispatch_custom_event("ws_frame", event, config=config)


# ─── Graph nodes ──────────────────────────────────────────────────────────────


def plan_tour_node(state: NarrationState) -> dict:
    """Build the ordered tour_nodes list from the loaded graph."""
    nodes: list[dict] = state["nodes"]
    edges: list[dict] = state["edges"]

    indegree, outdegree = _compute_degrees(nodes, edges)
    node_map = {n.get("id"): n for n in nodes}

    # Build adjacency map
    adj_map: dict[str, list[str]] = {}
    for edge in edges:
        src = edge.get("source")
        if src:
            adj_map.setdefault(src, []).append(edge.get("target", ""))

    entry_node     = _find_entry_node(nodes, indegree, outdegree)
    entry_filepath = entry_node.get("data", {}).get("filepath", "")

    same_file = _order_entry_file_nodes(nodes, entry_filepath, indegree, outdegree)

    visited    = {n.get("id") for n in same_file}
    bfs_queue  = list(visited)
    cross_file: list[dict] = []

    while bfs_queue:
        cid = bfs_queue.pop(0)
        for child_id in sorted(adj_map.get(cid, []), key=lambda x: -outdegree.get(x, 0)):
            if child_id in visited:
                continue
            child = node_map.get(child_id)
            if not child or not child.get("data", {}).get("code", "").strip():
                continue
            visited.add(child_id)
            cross_file.append(child)
            bfs_queue.append(child_id)

    tour_nodes = same_file + cross_file[:20]

    # Send intro text (dispatched later in explain_node, just set state here)
    return {"tour_nodes": tour_nodes, "current_index": 0, "user_message": None}


async def explain_node_fn(state: NarrationState, config: RunnableConfig) -> dict:
    """Stream focus frame + LLM explanation for tour_nodes[current_index]."""
    idx  = state["current_index"]
    node = state["tour_nodes"][idx]
    data = node.get("data", {})

    label     = data.get("label", "Unknown")
    filepath  = data.get("filepath", "")
    node_type = data.get("type", "function")
    code      = data.get("code", "").strip()

    # Focus frame ─ frontend will highlight this node in the graph
    await _dispatch({
        "type":     "focus",
        "node_id":  node.get("id", ""),
        "label":    label,
        "filepath": filepath,
    }, config)

    # Section header (only show filepath when it changes — tracked client-side)
    await _dispatch({
        "type":  "text",
        "chunk": f"\n### {idx + 1}. `{label}` ({node_type})\n\n",
    }, config)

    if not code:
        await _dispatch({"type": "text", "chunk": "_No code available._\n\n"}, config)
        return {}

    messages = [
        {
            "role":    "system",
            "content": (
                "You are a senior engineer giving a codebase walkthrough. "
                "Explain this function concisely in 2-3 sentences. "
                "Be direct and technical. No fluff."
            ),
        },
        {
            "role":    "user",
            "content": (
                f"**Name:** `{label}`\n\n"
                f"**Definition:**\n```\n{code[:1200]}\n```\n\n"
                "What does it do and what is the key logic?"
            ),
        },
    ]

    try:
        async for chunk in _llm().astream(messages):
            content = chunk.content if hasattr(chunk, "content") else str(chunk)
            if content:
                await _dispatch({"type": "text", "chunk": content}, config)
    except Exception as exc:
        log.warning("LLM failed for node %s: %s", label, exc)
        await _dispatch({"type": "text", "chunk": "_Explanation unavailable._"}, config)

    await _dispatch({"type": "text", "chunk": "\n\n"}, config)
    return {}


def pause_node(state: NarrationState) -> dict:
    """Pause the graph and wait for user input via WebSocket."""
    user_msg = interrupt({"type": "wait"})
    return {"user_message": user_msg}


async def answer_question_fn(state: NarrationState, config: RunnableConfig) -> dict:
    """Answer a user question scoped to the current node's code."""
    node  = state["tour_nodes"][state["current_index"]]
    data  = node.get("data", {})
    label = data.get("label", "this function")
    code  = data.get("code", "").strip()[:1500]
    question = (state["user_message"] or {}).get("text", "")

    await _dispatch({"type": "text", "chunk": f"**Q: {question}**\n\n"}, config)

    messages = [
        {
            "role":    "system",
            "content": "You are a senior engineer. Answer only using the provided code context. Be concise.",
        },
        {
            "role":    "user",
            "content": (
                f"Code (`{label}`):\n```\n{code}\n```\n\n"
                f"Question: {question}"
            ),
        },
    ]

    try:
        async for chunk in _llm().astream(messages):
            content = chunk.content if hasattr(chunk, "content") else str(chunk)
            if content:
                await _dispatch({"type": "text", "chunk": content}, config)
    except Exception as exc:
        log.warning("LLM failed answering question: %s", exc)
        await _dispatch({"type": "text", "chunk": "_Unable to answer._"}, config)

    await _dispatch({"type": "text", "chunk": "\n\n"}, config)
    # Reset user_message so routing falls through to advance
    return {"user_message": {"type": "continue"}}


def change_focus_fn(state: NarrationState) -> dict:
    """Jump the tour to a user-requested node_id."""
    target_id  = (state.get("user_message") or {}).get("node_id", "")
    tour_nodes = list(state["tour_nodes"])
    cur_idx    = state["current_index"]

    if not target_id:
        return {"user_message": {"type": "continue"}}

    # Already scheduled in tour?
    for pos, n in enumerate(tour_nodes):
        if n.get("id") == target_id:
            return {"current_index": pos, "user_message": None}

    # Find in full graph and insert at current position
    target_node = next((n for n in state["nodes"] if n.get("id") == target_id), None)
    if not target_node or not target_node.get("data", {}).get("code", "").strip():
        return {"user_message": {"type": "continue"}}

    tour_nodes.insert(cur_idx, target_node)
    return {"tour_nodes": tour_nodes, "current_index": cur_idx, "user_message": None}


def advance_fn(state: NarrationState) -> dict:
    """Advance the tour index by one."""
    return {"current_index": state["current_index"] + 1, "user_message": None}


# ─── Conditional edges ────────────────────────────────────────────────────────


def route_after_input(state: NarrationState) -> str:
    msg      = state.get("user_message") or {}
    msg_type = msg.get("type", "continue")
    if msg_type == "question" and msg.get("text"):
        return "answer_question"
    if msg_type == "focus" and msg.get("node_id"):
        return "change_focus"
    return "advance"


def check_if_done(state: NarrationState) -> str:
    if state["current_index"] >= len(state["tour_nodes"]):
        return "__end__"
    return "explain_node"


# ─── Build and compile the graph ──────────────────────────────────────────────


def _build_graph() -> Any:
    builder: StateGraph = StateGraph(NarrationState)

    builder.add_node("plan_tour",        plan_tour_node)
    builder.add_node("explain_node",     explain_node_fn)
    builder.add_node("pause",            pause_node)
    builder.add_node("route_input",      lambda s: s)  # pass-through; routing via conditional edge
    builder.add_node("answer_question",  answer_question_fn)
    builder.add_node("change_focus",     change_focus_fn)
    builder.add_node("advance",          advance_fn)

    # Entry
    builder.set_entry_point("plan_tour")

    # Linear flow
    builder.add_edge("plan_tour",   "explain_node")
    builder.add_edge("explain_node", "pause")
    builder.add_edge("pause",        "route_input")

    # Conditional after input routing
    builder.add_conditional_edges(
        "route_input",
        route_after_input,
        {
            "advance":         "advance",
            "answer_question": "answer_question",
            "change_focus":    "change_focus",
        },
    )

    # After answering, advance (route_input is not in the path; user_message is reset to "continue")
    builder.add_edge("answer_question", "advance")

    # After focus change, explain the newly focused node
    builder.add_edge("change_focus", "explain_node")

    # After advancing, either loop back or end
    builder.add_conditional_edges(
        "advance",
        check_if_done,
        {"explain_node": "explain_node", "__end__": END},
    )

    checkpointer = MemorySaver()
    return builder.compile(checkpointer=checkpointer)


# Lazily compiled graph — avoids LangGraph compilation cost at import time.
_compiled_graph: "Any | None" = None


def _get_compiled_graph() -> Any:
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = _build_graph()
    return _compiled_graph


def _cleanup_thread(config: dict) -> None:
    """Remove this session's checkpoint data from MemorySaver to prevent unbounded growth."""
    try:
        graph = _get_compiled_graph()
        thread_id = config.get("configurable", {}).get("thread_id", "")
        if not thread_id:
            return
        checkpointer = graph.checkpointer
        for attr in ("storage", "writes"):
            store = getattr(checkpointer, attr, None)
            if isinstance(store, dict):
                stale = [k for k in list(store) if isinstance(k, tuple) and k and k[0] == thread_id]
                for k in stale:
                    store.pop(k, None)
    except Exception:
        pass


# ─── Public API ───────────────────────────────────────────────────────────────


async def run_narration_graph(ws: WebSocket, graph_cache: dict) -> None:
    """
    Interactive codebase tour via LangGraph state machine.

    Pauses after each node explanation and waits for user input:
      {"type":"continue"}             — advance to next node
      {"type":"question","text":"..."}— ask a question about the current node
      {"type":"focus","node_id":"..."}— jump focus to a different node
    """
    nodes: list[dict] = graph_cache.get("nodes", [])
    edges: list[dict] = graph_cache.get("edges", [])

    if not nodes:
        await ws.send_text(json.dumps({"type": "error", "message": "No graph loaded. Analyze a codebase first."}))
        return

    initial_state: NarrationState = {
        "nodes":         nodes,
        "edges":         edges,
        "tour_nodes":    [],
        "current_index": 0,
        "user_message":  None,
    }

    config:       dict = {"configurable": {"thread_id": str(uuid4())}}
    input_or_cmd: Any = initial_state
    graph = _get_compiled_graph()

    try:
        # Send intro header
        entry_node    = _find_entry_node(nodes, *_compute_degrees(nodes, edges))
        entry_filepath = entry_node.get("data", {}).get("filepath", "")
        await ws.send_text(json.dumps({"type": "text", "chunk": "# Codebase Tour\n\n"}))
        await ws.send_text(json.dumps({"type": "text", "chunk": f"Starting from **`{entry_filepath}`**\n\n---\n\n"}))

        while True:
            # Run (or resume) the graph and forward custom events to the WS
            async for event in graph.astream_events(
                input_or_cmd, config=config, version="v2"
            ):
                if event.get("event") == "on_custom_event" and event.get("name") == "ws_frame":
                    await ws.send_text(json.dumps(event["data"]))

            # Check whether the graph completed or hit an interrupt
            snapshot = graph.get_state(config)
            if not snapshot.next:
                break  # tour complete

            # Interrupted at pause node — wait for user input
            await ws.send_text(json.dumps({
                "type":    "wait",
                "message": 'Continue the tour or interact: {"type":"continue"} | {"type":"question","text":"..."} | {"type":"focus","node_id":"..."}',
            }))

            try:
                user_msg = await asyncio.wait_for(ws.receive_json(), timeout=120.0)
            except asyncio.TimeoutError:
                user_msg = {"type": "continue"}

            input_or_cmd = Command(resume=user_msg)

        # Tour complete
        await ws.send_text(json.dumps({
            "type":  "text",
            "chunk": "\n---\n\n## Tour Complete!\n\nClick any node and press **Explain This Node** to explore further.\n",
        }))
        await ws.send_text(json.dumps({"type": "complete"}))

    except _CLOSED:
        log.info("WS client disconnected during LangGraph narration")
    except Exception as exc:
        log.exception("LangGraph narration error")
        try:
            await ws.send_text(json.dumps({"type": "error", "message": str(exc)}))
        except Exception:
            pass
    finally:
        _cleanup_thread(config)


async def run_node_narration_graph(ws: WebSocket, graph_cache: dict, node_id: str) -> None:
    """
    Single-node deep-dive narration.
    Delegates to the existing non-interactive narrator for simplicity.
    """
    from shared.narrator import run_node_narration
    await run_node_narration(ws, graph_cache, node_id)
