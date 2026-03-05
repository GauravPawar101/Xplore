"""
LLM provider abstraction — OpenAI, Anthropic, Ollama.

Resolves API key from request body (api_keys) or from config (env).
"""

import logging
from typing import Any, Optional

from shared.config import ANTHROPIC_API_KEY, OPENAI_API_KEY, HUGGINGFACE_TOKEN, HUGGINGFACE_TOKEN0, HF_MODEL_ID

log = logging.getLogger("ezdocs.llm")
c = 0
HF_TOKEN = HUGGINGFACE_TOKEN if c % 2 == 0 else HUGGINGFACE_TOKEN0
c += 1
DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-haiku-20241022",
    "huggingface": HF_MODEL_ID,
}


def _get_key(provider: str, api_keys: Optional[Any]) -> Optional[str]:
    if api_keys:
        if provider == "openai" and getattr(api_keys, "openai", None):
            return api_keys.openai
        if provider == "anthropic" and getattr(api_keys, "anthropic", None):
            return api_keys.anthropic
        if provider == "huggingface" and getattr(api_keys, "huggingface", None):
            return api_keys.huggingface
    if provider == "openai":
        return OPENAI_API_KEY or None
    if provider == "anthropic":
        return ANTHROPIC_API_KEY or None
    if provider == "huggingface":
        return HF_TOKEN or None
    return None


async def completion(
    provider: str,
    messages: list[dict[str, str]],
    *,
    model: Optional[str] = None,
    api_keys: Optional[Any] = None,
    max_tokens: int = 4096,
) -> str:
    model = model or DEFAULT_MODELS.get(provider, "")
    if provider == "huggingface":
        key = _get_key("huggingface", api_keys)
        if not key:
            raise ValueError("Hugging Face API token required. Set HUGGINGFACE_HUB_TOKEN or pass api_keys.huggingface.")
        return await _hf_completion(messages, model=model, api_key=key)
    if provider == "openai":
        key = _get_key("openai", api_keys)
        if not key:
            raise ValueError("OpenAI API key required. Set OPENAI_API_KEY or pass api_keys.openai.")
        return await _openai_completion(messages, model=model, api_key=key, max_tokens=max_tokens)
    if provider == "anthropic":
        key = _get_key("anthropic", api_keys)
        if not key:
            raise ValueError("Anthropic API key required. Set ANTHROPIC_API_KEY or pass api_keys.anthropic.")
        return await _anthropic_completion(messages, model=model, api_key=key, max_tokens=max_tokens)
    raise ValueError(f"Unknown provider: {provider}")


async def _hf_completion(
    messages: list[dict[str, str]],
    model: str,
    api_key: str,
) -> str:
    import httpx
    url = f"https://api-inference.huggingface.co/models/{model}"
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"messages": messages, "max_tokens": 4096},
        )
        if r.status_code == 503:
            raise RuntimeError("Hugging Face model is loading. Retry in a minute.")
        r.raise_for_status()
        data = r.json()
        if "choices" in data and data["choices"]:
            return (data["choices"][0].get("message") or {}).get("content") or ""
        if "generated_text" in data:
            return data["generated_text"]
        if isinstance(data, list) and len(data) > 0 and "generated_text" in data[0]:
            return data[0]["generated_text"]
        raise RuntimeError(f"Unexpected HF response format: {data}")


async def _openai_completion(
    messages: list[dict[str, str]],
    model: str,
    api_key: str,
    max_tokens: int,
) -> str:
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=api_key)
    resp = await client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content or ""


async def _anthropic_completion(
    messages: list[dict[str, str]],
    model: str,
    api_key: str,
    max_tokens: int,
) -> str:
    from anthropic import AsyncAnthropic
    system = ""
    msgs = []
    for m in messages:
        if m.get("role") == "system":
            system = m.get("content", "")
        else:
            msgs.append({"role": m["role"], "content": m["content"]})
    client = AsyncAnthropic(api_key=api_key)
    resp = await client.messages.create(
        model=model,
        system=system or None,
        messages=msgs,
        max_tokens=max_tokens,
    )
    return resp.content[0].text if resp.content else ""
