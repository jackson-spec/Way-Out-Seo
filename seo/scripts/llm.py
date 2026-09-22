"""Thin REST clients for the providers in config/providers.json.

providers.json is the only place provider names/models live; this module only
knows provider *kinds* (gemini, openai_compatible, anthropic, exa).

Guardrail: never add a second account on a provider already configured to get
more free quota. When a provider's own rate limit is hit, call_with_fallback
routes that one call through OpenRouter (a genuinely different provider).
"""
from __future__ import annotations

import time

import requests

from common import env, load_config

TIMEOUT = 60


class RateLimited(Exception):
    pass


def _post(url: str, headers: dict, body: dict) -> dict:
    r = requests.post(url, headers=headers, json=body, timeout=TIMEOUT)
    if r.status_code == 429:
        raise RateLimited(r.text[:200])
    r.raise_for_status()
    return r.json()


def _gemini(p: dict, key: str, prompt: str, max_tokens: int) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{p['model']}:generateContent"
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens}}
    data = _post(url, {"x-goog-api-key": key}, body)
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return "".join(part.get("text", "") for part in parts)


def _openai_compatible(p: dict, key: str, prompt: str, max_tokens: int, model: str | None = None) -> str:
    url = p.get("base_url", "https://openrouter.ai/api/v1").rstrip("/") + "/chat/completions"
    body = {"model": model or p["model"], "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}]}
    data = _post(url, {"Authorization": f"Bearer {key}"}, body)
    return data["choices"][0]["message"]["content"] or ""


def _anthropic(p: dict, key: str, prompt: str, max_tokens: int) -> str:
    body = {"model": p["model"], "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}]}
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    data = _post("https://api.anthropic.com/v1/messages", headers, body)
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


def _exa(p: dict, key: str, prompt: str, max_tokens: int) -> str:
    """Exa is a neural search index, not an LLM: return result URLs + titles as text."""
    body = {"query": prompt, "numResults": 10, "type": "auto"}
    data = _post("https://api.exa.ai/search", {"x-api-key": key}, body)
    return "\n".join(f"{r.get('title', '')} {r.get('url', '')}" for r in data.get("results", []))


CALLERS = {"gemini": _gemini, "openai_compatible": _openai_compatible,
           "anthropic": _anthropic, "exa": _exa}


def providers(role: str | None = None) -> list[dict]:
    """Configured providers whose API key is present (optionally filtered by role)."""
    out = []
    for p in load_config("providers"):
        if role and role not in p.get("roles", []):
            continue
        if env(p["key_env"]):
            out.append(p)
    return out


def missing_providers() -> list[str]:
    return [f"{p['name']} ({p['key_env']})" for p in load_config("providers") if not env(p["key_env"])]


def openrouter() -> dict | None:
    for p in load_config("providers"):
        if p["kind"] == "openai_compatible" and "openrouter" in p.get("base_url", "") and env(p["key_env"]):
            return p
    return None


def call(p: dict, prompt: str, max_tokens: int = 800) -> str:
    return CALLERS[p["kind"]](p, env(p["key_env"]), prompt, max_tokens)


def call_with_fallback(p: dict, prompt: str, max_tokens: int = 800) -> tuple[str, str]:
    """Return (text, route). On a 429, retry once after a pause, then route via OpenRouter."""
    try:
        return call(p, prompt, max_tokens), "direct"
    except RateLimited:
        time.sleep(10)
    try:
        return call(p, prompt, max_tokens), "direct-retry"
    except RateLimited:
        orp = openrouter()
        fallback_model = p.get("openrouter_fallback_model")
        if not orp or not fallback_model or orp["name"] == p["name"]:
            raise
        text = _openai_compatible(orp, env(orp["key_env"]), prompt, max_tokens, model=fallback_model)
        return text, f"openrouter:{fallback_model}"
