"""WebSocket endpoints for AI narrator (codebase tour and per-node deep-dive)."""

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import shared.narrator as narrator
from shared.state import graph_cache

from shared import db

log = logging.getLogger("ezdocs")

router = APIRouter(tags=["narrator"])


@router.websocket("/ws/narrate")
async def ws_narrate(websocket: WebSocket) -> None:
    """AI Narrator: guided tour through the entire codebase.
    Optional first message: {\"codebase_id\": \"...\"} to load graph from DB (microservices mode)."""
    await websocket.accept()
    graph = graph_cache.get("graph")
    codebase_id = None
    if (not graph or not graph.get("nodes")) and db._is_available_sync():
        try:
            msg = await asyncio.wait_for(websocket.receive_json(), timeout=2.0)
            codebase_id = (msg or {}).get("codebase_id")
        except (asyncio.TimeoutError, Exception):
            pass
        if codebase_id:
            graph = await db.read_codebase_graph(codebase_id)
    if not graph or not graph.get("nodes"):
        try:
            await websocket.send_text(json.dumps({
                "type": "text",
                "chunk": "❌ No graph loaded. Run **Run Analysis** first, then click **Start Tour**."
            }))
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass
        return
    try:
        await narrator.run_narration(websocket, graph)
    except WebSocketDisconnect:
        log.info("WS client disconnected during narration")
    except Exception as exc:
        log.exception("WS narration error")
        try:
            await websocket.send_text(json.dumps({
                "type": "text",
                "chunk": f"\n\n❌ Error: {exc}"
            }))
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


@router.websocket("/ws/narrate/node")
async def ws_narrate_node(websocket: WebSocket) -> None:
    """Stream a deep-dive narration for a single node. Expects JSON: {\"node_id\": \"...\", \"codebase_id\": \"...\" (optional)}."""
    await websocket.accept()
    try:
        data = await asyncio.wait_for(websocket.receive_json(), timeout=15)
        node_id: str = (data.get("node_id") or "").strip()
        codebase_id = (data.get("codebase_id") or "").strip() or None
        if not node_id:
            await websocket.send_text('{"type":"text","chunk":"❌ Error: no node_id provided."}')
            return
        graph = graph_cache.get("graph")
        if (not graph or not graph.get("nodes")) and codebase_id and db._is_available_sync():
            graph = await db.read_codebase_graph(codebase_id)
        if not graph or not graph.get("nodes"):
            await websocket.send_text(
                '{"type":"text","chunk":"❌ No graph loaded. Run an analysis first."}'
            )
            return
        log.info("WS node narration requested: %s", node_id)
        await narrator.run_node_narration(websocket, graph, node_id)
    except asyncio.TimeoutError:
        try:
            await websocket.send_text(
                '{"type":"text","chunk":"❌ Error: timed out waiting for request."}'
            )
        except Exception:
            pass
    except WebSocketDisconnect:
        log.info("WS client disconnected during node narration")
    except Exception as exc:
        log.exception("WS node narration error")
        try:
            await websocket.send_text(json.dumps({
                "type": "text",
                "chunk": f"❌ Error: {exc}"
            }))
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
