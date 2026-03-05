"""AI explanation and chat endpoints (REST and WebSocket)."""

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from shared import ai
from shared.schemas import ExplainRequest

log = logging.getLogger("ezdocs")

router = APIRouter(tags=["ai"])


@router.post("/explain")
async def explain_code(request: ExplainRequest) -> dict:
    """Return a one-shot AI explanation for a code snippet (optional caller/callee context)."""
    try:
        deps = request.callees or []
        callers = request.callers or []
        explanation = ai.generate_explanation(
            request.code, deps, filepath=request.context, callers=callers
        )
        return {"explanation": explanation, "original_code": request.code}
    except Exception as exc:
        log.exception("Explanation failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.websocket("/ws/explain")
async def ws_explain(websocket: WebSocket) -> None:
    """Stream code explanation over WebSocket."""
    await websocket.accept()
    try:
        data: dict = await asyncio.wait_for(websocket.receive_json(), timeout=15)
        code: str = (data.get("code") or "").strip()
        context: str = (data.get("context") or "").strip()
        callers: list[str] = data.get("callers") or []
        callees: list[str] = data.get("callees") or []
        if not code:
            await websocket.send_text("No code provided. Select a node with code first.")
            return
        log.info("WS explanation requested (context=%s)", context or "<none>")
        async for chunk in ai.generate_explanation_stream(code, callees, filepath=context, callers=callers):
            await websocket.send_text(chunk)
    except asyncio.TimeoutError:
        await websocket.send_text("Request timed out. Try again.")
    except WebSocketDisconnect:
        log.info("WS client disconnected during explanation")
    except Exception as exc:
        log.exception("WS explanation error")
        try:
            err_msg = str(exc).strip() or "Explanation failed"
            if "ollama" in err_msg.lower() or "hugging face" in err_msg.lower() or "unreachable" in err_msg.lower() or "connection" in err_msg.lower():
                await websocket.send_text("Unable to generate explanation. Check that **Ollama** is running (or set **HUGGINGFACE_HUB_TOKEN** for HF Inference) and the model is available.")
            else:
                await websocket.send_text(f"Unable to generate explanation: {err_msg}")
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


@router.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket) -> None:
    """
    Long-lived conversation: client sends plain text (one message per turn).
    Server keeps history in memory per connection; streams assistant reply as plain text.
    Errors are sent as a single JSON line: {"error": "..."}.
    """
    await websocket.accept()
    messages: list[dict[str, str]] = []
    try:
        while True:
            data = await asyncio.wait_for(websocket.receive_text(), timeout=300)
            text = (data or "").strip()
            if not text:
                continue
            messages.append({"role": "user", "content": text})
            accumulated: list[str] = []
            try:
                async for chunk in ai.chat_stream(messages):
                    accumulated.append(chunk)
                    await websocket.send_text(chunk)
                messages.append({"role": "assistant", "content": "".join(accumulated)})
                await websocket.send_text("\x01")  # end-of-stream marker (single byte)
            except ai.OllamaError as exc:
                log.warning("Chat Ollama error: %s", exc)
                await websocket.send_text(
                    json.dumps({"error": "Ollama is unavailable. Start Ollama and pull a model (e.g. ollama run qwen2.5-coder:3b)."})
                )
                continue
            except Exception as exc:
                log.exception("WS chat error")
                await websocket.send_text(json.dumps({"error": str(exc)}))
                continue
    except asyncio.TimeoutError:
        pass  # idle timeout, close connection
    except WebSocketDisconnect:
        log.info("WS chat client disconnected")
    except Exception as exc:
        log.exception("WS chat connection error")
        try:
            await websocket.send_text(json.dumps({"error": str(exc)}))
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
