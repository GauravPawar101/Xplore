"""
AI Layer — EzDocs

Provider priority:
  1. Ollama  — when OLLAMA_HOST is set (local Docker)
  2. HF router — fallback when HF_TOKEN is set

  STT  : api-inference.huggingface.co  (Whisper)
  TTS  : api-inference.huggingface.co  (MMS-TTS / Chatterbox)

.env keys:
  OLLAMA_HOST                       — e.g. http://ollama:11434
  EZDOCS_MODEL                      — e.g. qwen2.5-coder:3b (default)
  HF_TOKEN (or HF_TOKEN)            — HF access token (fallback)
  HF_MODEL_ID                       — e.g. Qwen/Qwen3.5-397B-A17B:novita
  TTS_MODEL                         — default: facebook/mms-tts-eng
  STT_MODEL                         — default: openai/whisper-large-v3
  STT_FAST=true                     — use distil-whisper instead
"""

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from typing import AsyncIterator

from shared.config import HUGGINGFACE_TOKEN, HUGGINGFACE_TOKEN0, HF_MODEL_ID, OLLAMA_HOST, OLLAMA_MODEL

log = logging.getLogger("ezdocs.ai")

# ─── Config ───────────────────────────────────────────────────────────────────
c = 0;
# Model for chat/explain — supports any HF router provider suffix e.g. :novita :hf-inference
HF_ROUTER_MODEL: str = os.getenv("HF_MODEL_ID", HF_MODEL_ID)
HF_TOKEN = HUGGINGFACE_TOKEN if c % 2 == 0 else HUGGINGFACE_TOKEN0;
c += 1
HF_MAX_TOKENS   = int(os.getenv("EZDOCS_HF_MAX_TOKENS",    "512"))
HF_BATCH_WORKERS= int(os.getenv("EZDOCS_HF_BATCH_WORKERS", "8"))
BATCH_CONCURRENCY=int(os.getenv("EZDOCS_BATCH_CONCURRENCY","4"))

TTS_MODEL      = os.getenv("TTS_MODEL",      "facebook/mms-tts-eng")
STT_MODEL      = os.getenv("STT_MODEL",      "openai/whisper-large-v3")
STT_MODEL_FAST = os.getenv("STT_MODEL_FAST", "distil-whisper/distil-large-v3")

_HF_TTS_TIMEOUT = 30.0
_HF_STT_TIMEOUT = 60.0


def _use_hf() -> bool:
    return bool(HF_TOKEN)


def _use_ollama() -> bool:
    return bool(OLLAMA_HOST)


# ─── Errors ───────────────────────────────────────────────────────────────────

class AIProviderError(RuntimeError):
    """Raised when the LLM backend is unreachable or returns an error."""


# ─── OpenAI client (sync + async) ────────────────────────────────────────────

@lru_cache(maxsize=1)
def _sync_client():
    """Cached sync OpenAI client pointing at HF router."""
    from openai import OpenAI
    return OpenAI(
        base_url="https://router.huggingface.co/v1",
        api_key=HF_TOKEN,
    )


@lru_cache(maxsize=1)
def _async_client():
    """Cached async OpenAI client pointing at HF router."""
    from openai import AsyncOpenAI
    return AsyncOpenAI(
        base_url="https://router.huggingface.co/v1",
        api_key=HF_TOKEN,
    )


# ─── Ollama client (sync + async, OpenAI-compatible endpoint) ─────────────────

@lru_cache(maxsize=1)
def _ollama_sync_client():
    from openai import OpenAI
    return OpenAI(base_url=f"{OLLAMA_HOST}/v1", api_key="ollama")


@lru_cache(maxsize=1)
def _ollama_async_client():
    from openai import AsyncOpenAI
    return AsyncOpenAI(base_url=f"{OLLAMA_HOST}/v1", api_key="ollama")


def _ollama_chat_sync(messages: list[dict], max_tokens: int = 512) -> str:
    try:
        resp = _ollama_sync_client().chat.completions.create(
            model=OLLAMA_MODEL,
            messages=messages,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""
    except Exception as exc:
        raise AIProviderError(f"Ollama error: {exc}") from exc


async def _ollama_chat_stream(messages: list[dict], max_tokens: int = 512):
    try:
        stream = await _ollama_async_client().chat.completions.create(
            model=OLLAMA_MODEL,
            messages=messages,
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
    except Exception as exc:
        raise AIProviderError(f"Ollama streaming error: {exc}") from exc


# ─── Prompts ──────────────────────────────────────────────────────────────────

_SYSTEM = (
    "You are a senior software engineer performing a precise code review. "
    "Be direct, technical, and concise. Use Markdown."
)

_EXPLAIN_TMPL = """\
Explain the following {lang}code definition.

Cover exactly these four points — no more, no less:
1. **Purpose** — one sentence on what it does.
2. **Key logic** — the most important steps or decisions.
3. **Dependencies / side-effects** — what it calls or mutates externally.
4. **Concerns** — edge cases, complexity, or anything worth flagging.

{context_block}```
{code}
```"""

_SUMMARY_TMPL = """\
Summarise the purpose of the following code in 2–4 plain-text sentences.
Focus on *what* it does and *why* it exists. No implementation detail, no markdown.

{context_block}```
{code}
```"""


def _context_block(callees: list[str], callers: list[str]) -> str:
    parts = []
    if callers:
        parts.append(f"**Callers (who calls this):** {', '.join(f'`{c}`' for c in callers)}")
    if callees:
        parts.append(f"**Callees (what this calls):** {', '.join(f'`{c}`' for c in callees)}")
    return ("\n\n".join(parts) + "\n\n") if parts else ""


def _build_messages(
    template: str,
    code: str,
    callees: list[str],
    filepath: str = "",
    callers: list[str] | None = None,
) -> list[dict]:
    callers = callers or []
    lang = f"`{filepath}` — " if filepath else ""
    context_block = _context_block(callees, callers)
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": template.format(lang=lang, context_block=context_block, code=code)},
    ]


# ─── Core chat helpers (used by narrator, explain, summary, chat) ─────────────

def _hf_chat_sync(messages: list[dict], max_tokens: int = HF_MAX_TOKENS) -> str:
    """Blocking chat completion via OpenAI client → HF router."""
    try:
        resp = _sync_client().chat.completions.create(
            model=HF_ROUTER_MODEL,
            messages=messages,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""
    except Exception as exc:
        if "503" in str(exc):
            raise AIProviderError("HF model is loading. Retry in a minute.") from exc
        raise AIProviderError(f"HF inference error: {exc}") from exc


async def _hf_chat_stream(messages: list[dict], max_tokens: int = HF_MAX_TOKENS) -> AsyncIterator[str]:
    """Streaming chat completion via async OpenAI client → HF router."""
    try:
        stream = await _async_client().chat.completions.create(
            model=HF_ROUTER_MODEL,
            messages=messages,
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
    except Exception as exc:
        if "503" in str(exc):
            raise AIProviderError("HF model is loading. Retry in a minute.") from exc
        raise AIProviderError(f"HF streaming error: {exc}") from exc


# ─── STT (Whisper — still on old api-inference endpoint) ─────────────────────

def transcribe(audio_bytes: bytes, *, fast: bool = False, language: str | None = None) -> str:
    """
    Transcribe audio bytes → text via HF Inference API (Whisper).

    Args:
        audio_bytes: wav / mp3 / flac / ogg
        fast:        distil-whisper (6x faster, ~1% WER trade-off)
        language:    ISO 639-1 hint e.g. "en", "fr", "hi"
    """
    import requests
    use_fast = fast or os.getenv("STT_FAST", "").lower() in ("1", "true", "yes")
    model = STT_MODEL_FAST if use_fast else STT_MODEL
    url = f"https://api-inference.huggingface.co/models/{model}"
    headers = {"Authorization": f"Bearer {HF_TOKEN}", "Content-Type": "audio/wav"}
    params = {"language": language} if language else {}
    try:
        r = requests.post(url, headers=headers, data=audio_bytes, params=params, timeout=_HF_STT_TIMEOUT)
        if r.status_code == 503:
            raise AIProviderError(f"STT model {model} loading. Retry shortly.")
        r.raise_for_status()
        return r.json().get("text", "").strip()
    except AIProviderError:
        raise
    except Exception as exc:
        raise AIProviderError(f"STT error: {exc}") from exc


async def transcribe_async(audio_bytes: bytes, *, fast: bool = False, language: str | None = None) -> str:
    """Async version of transcribe()."""
    import httpx
    use_fast = fast or os.getenv("STT_FAST", "").lower() in ("1", "true", "yes")
    model = STT_MODEL_FAST if use_fast else STT_MODEL
    url = f"https://api-inference.huggingface.co/models/{model}"
    headers = {"Authorization": f"Bearer {HF_TOKEN}", "Content-Type": "audio/wav"}
    params = {"language": language} if language else {}
    async with httpx.AsyncClient(timeout=_HF_STT_TIMEOUT) as client:
        r = await client.post(url, headers=headers, content=audio_bytes, params=params)
        if r.status_code == 503:
            raise AIProviderError(f"STT model {model} loading. Retry shortly.")
        r.raise_for_status()
        return r.json().get("text", "").strip()


# ─── TTS (MMS-TTS / Chatterbox — still on old api-inference endpoint) ─────────

def synthesize(text: str, *, model: str | None = None) -> bytes:
    """
    Text → speech audio bytes (wav) via HF Inference API.

    Models (set TTS_MODEL in .env):
        facebook/mms-tts-eng       — fast, lightweight (default)
        resemble-ai/chatterbox     — natural voice, emotion control
    """
    import requests
    tts_model = model or TTS_MODEL
    url = f"https://api-inference.huggingface.co/models/{tts_model}"
    headers = {"Authorization": f"Bearer {HF_TOKEN}", "Content-Type": "application/json"}
    try:
        r = requests.post(url, headers=headers, json={"inputs": text}, timeout=_HF_TTS_TIMEOUT)
        if r.status_code == 503:
            raise AIProviderError(f"TTS model {tts_model} loading. Retry shortly.")
        r.raise_for_status()
        return r.content
    except AIProviderError:
        raise
    except Exception as exc:
        raise AIProviderError(f"TTS error: {exc}") from exc


async def synthesize_async(text: str, *, model: str | None = None) -> bytes:
    """Async version of synthesize()."""
    import httpx
    tts_model = model or TTS_MODEL
    url = f"https://api-inference.huggingface.co/models/{tts_model}"
    headers = {"Authorization": f"Bearer {HF_TOKEN}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=_HF_TTS_TIMEOUT) as client:
        r = await client.post(url, headers=headers, json={"inputs": text})
        if r.status_code == 503:
            raise AIProviderError(f"TTS model {tts_model} loading. Retry shortly.")
        r.raise_for_status()
        return r.content


# ─── Streaming explanation ────────────────────────────────────────────────────

async def generate_explanation_stream(
    code: str,
    dependencies: list[str],
    filepath: str = "",
    model: str = HF_ROUTER_MODEL,
    *,
    callers: list[str] | None = None,
) -> AsyncIterator[str]:
    messages = _build_messages(_EXPLAIN_TMPL, code, dependencies, filepath, callers=callers)
    if _use_ollama():
        try:
            async for chunk in _ollama_chat_stream(messages):
                yield chunk
            return
        except AIProviderError as exc:
            if not _use_hf():
                raise
            log.warning("Ollama stream failed, falling back to HF: %s", exc)

    if _use_hf():
        async for chunk in _hf_chat_stream(messages):
            yield chunk
        return

    raise AIProviderError("No AI backend configured. Set OLLAMA_HOST or HF_TOKEN.")


# ─── Blocking explanation ─────────────────────────────────────────────────────

def generate_explanation(
    code: str,
    dependencies: list[str],
    filepath: str = "",
    model: str = HF_ROUTER_MODEL,
    *,
    callers: list[str] | None = None,
) -> str:
    messages = _build_messages(_EXPLAIN_TMPL, code, dependencies, filepath, callers=callers)
    if _use_ollama():
        try:
            return _ollama_chat_sync(messages)
        except AIProviderError as exc:
            if not _use_hf():
                raise
            log.warning("Ollama explain failed, falling back to HF: %s", exc)
    if _use_hf():
        return _hf_chat_sync(messages)
    raise AIProviderError("No AI backend configured. Set OLLAMA_HOST or HF_TOKEN.")


# ─── Short summary ────────────────────────────────────────────────────────────

def generate_summary(
    code: str,
    dependencies: list[str],
    filepath: str = "",
    model: str = HF_ROUTER_MODEL,
    *,
    callers: list[str] | None = None,
) -> str:
    messages = _build_messages(_SUMMARY_TMPL, code, dependencies, filepath, callers=callers)
    if _use_ollama():
        try:
            text = _ollama_chat_sync(messages, max_tokens=256)
        except AIProviderError as exc:
            if not _use_hf():
                raise
            log.warning("Ollama summary failed, falling back to HF: %s", exc)
            text = _hf_chat_sync(messages, max_tokens=256)
    elif _use_hf():
        text = _hf_chat_sync(messages, max_tokens=256)
    else:
        raise AIProviderError("No AI backend configured. Set OLLAMA_HOST or HF_TOKEN.")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines[:4])


# ─── Prefetch (batched parallel summaries) ───────────────────────────────────

def _caller_callee_maps(nodes: list[dict], edges: list[dict] | None):
    id_to_label: dict[str, str] = {}
    for n in nodes:
        nid = n.get("id", "")
        label = (n.get("data") or {}).get("label", nid)
        id_to_label[nid] = label
    callers: dict[str, list[str]] = {n.get("id", ""): [] for n in nodes}
    callees: dict[str, list[str]] = {n.get("id", ""): [] for n in nodes}
    if not edges:
        return callers, callees
    for e in edges:
        src = e.get("source", "")
        tgt = e.get("target", "")
        if not src or not tgt:
            continue
        callers.setdefault(tgt, []).append(id_to_label.get(src, src))
        callees.setdefault(src, []).append(id_to_label.get(tgt, tgt))
    return callers, callees


def prefetch_explanations_sync(
    nodes: list[dict],
    edges: list[dict] | None = None,
    model: str = HF_ROUTER_MODEL,
    *,
    batch_workers: int = HF_BATCH_WORKERS,
) -> None:
    callers_map, callees_map = _caller_callee_maps(nodes, edges)
    to_process = [
        (i, n) for i, n in enumerate(nodes)
        if (n.get("data") or {}).get("code", "").strip()
    ]
    if not to_process:
        return

    def _one(args: tuple) -> tuple[str, str]:
        idx, node = args
        node_id = node.get("id", "")
        data = node.get("data", {})
        try:
            summary = generate_summary(
                data.get("code", "").strip(),
                callees_map.get(node_id, []),
                data.get("filepath", ""),
                callers=callers_map.get(node_id, []),
            )
            return node_id, summary
        except Exception as exc:
            log.warning("Prefetch failed for %s: %s", node_id, exc)
            return node_id, ""

    done = 0
    with ThreadPoolExecutor(max_workers=batch_workers) as pool:
        futures = {pool.submit(_one, item): item for item in to_process}
        for fut in as_completed(futures):
            try:
                nid, summary = fut.result()
                for n in nodes:
                    if n.get("id") == nid:
                        (n.get("data") or n.setdefault("data", {}))["explanation"] = summary
                        break
            except Exception as exc:
                log.warning("Prefetch task failed: %s", exc)
            done += 1
            if done % 50 == 0:
                log.info("Prefetched %d / %d nodes", done, len(to_process))


# ─── Batch async explain ──────────────────────────────────────────────────────

async def explain_graph(
    nodes: list[dict],
    edges: list[dict] | None = None,
    model: str = HF_ROUTER_MODEL,
    max_concurrent: int = BATCH_CONCURRENCY,
) -> dict[str, str]:
    callers_map, callees_map = _caller_callee_maps(nodes, edges)
    sem = asyncio.Semaphore(max_concurrent)
    results: dict[str, str] = {}

    async def _one(node: dict) -> None:
        node_id = node.get("id", "")
        data = node.get("data", {})
        code = data.get("code", "").strip()
        if not code:
            results[node_id] = ""
            return
        async with sem:
            try:
                chunks: list[str] = []
                async for chunk in generate_explanation_stream(
                    code,
                    callees_map.get(node_id, []),
                    data.get("filepath", ""),
                    callers=callers_map.get(node_id, []),
                ):
                    chunks.append(chunk)
                results[node_id] = "".join(chunks)
            except AIProviderError as exc:
                log.warning("Inference failed for %s: %s", node_id, exc)
                results[node_id] = ""

    await asyncio.gather(*(_one(n) for n in nodes))
    return results


# ─── Chat ─────────────────────────────────────────────────────────────────────

_CHAT_SYSTEM = (
    "You are a helpful assistant for developers using EzDocs. "
    "Answer concisely. You can discuss code, architecture, and the codebase. Use Markdown when useful."
)


async def chat_stream(
    messages: list[dict[str, str]],
    model: str = HF_ROUTER_MODEL,
) -> AsyncIterator[str]:
    if not messages:
        return
    if not any(m.get("role") == "system" for m in messages):
        messages = [{"role": "system", "content": _CHAT_SYSTEM}] + list(messages)
    if _use_ollama():
        try:
            async for chunk in _ollama_chat_stream(messages):
                yield chunk
            return
        except AIProviderError as exc:
            if not _use_hf():
                raise
            log.warning("Ollama chat failed, falling back to HF: %s", exc)

    if _use_hf():
        async for chunk in _hf_chat_stream(messages):
            yield chunk
        return

    raise AIProviderError("No AI backend configured. Set OLLAMA_HOST or HF_TOKEN.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    _code = "def add(a: int, b: int) -> int:\n    return a + b"
    print(generate_explanation(_code, [], filepath="math_utils.py"))