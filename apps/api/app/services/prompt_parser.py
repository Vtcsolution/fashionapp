"""OpenAI prompt understanding: "beige jacket, white dress, ..." -> up to 6 structured outfit items.

Text-only call. It never generates images. On any problem (no key, HTTP error, bad JSON) it falls back to
the offline parser, so search keeps working; the returned source says which one produced the items.
"""
import json
import logging

import httpx

from ..config import settings
from .categorize import categorize
from .prompt_items import CATEGORIES, MAX_ITEMS, PromptItem, fallback_items

log = logging.getLogger(__name__)

SYSTEM = (
    "You split a shopper's outfit description into separate fashion items to search for in an online store. "
    f"Return at most {MAX_ITEMS} items, one per distinct garment/shoe/bag/accessory the shopper asked for, in the "
    "order mentioned. 'query' is a short store search phrase that keeps the shopper's color/material/style words "
    "(e.g. 'brown leather handbag'). Do not add items the shopper did not ask for. Do not merge two items. "
    "Ignore budget phrases like 'under $100'. 'category' must be one of: " + ", ".join(CATEGORIES) + "."
)

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["query", "category"],
                "properties": {"query": {"type": "string"}, "category": {"type": "string", "enum": CATEGORIES}},
            },
        }
    },
}


def _clean(data: dict) -> list[PromptItem]:
    out: list[PromptItem] = []
    for it in (data.get("items") or [])[:MAX_ITEMS]:
        q = str(it.get("query", "")).strip()[:80]
        if not q:
            continue
        cat = it.get("category")
        out.append(PromptItem(q, cat if cat in CATEGORIES else categorize(q)))
    return out


async def parse_items(text: str, transport: httpx.AsyncBaseTransport | None = None) -> tuple[list[PromptItem], str]:
    """Return (items, source) where source is 'openai' or 'fallback'."""
    if not settings.openai_api_key or not text.strip():
        return fallback_items(text), "fallback"
    try:
        async with httpx.AsyncClient(timeout=30, transport=transport) as c:
            r = await c.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                json={
                    "model": settings.openai_model,
                    "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text.strip()[:500]}],
                    "response_format": {"type": "json_schema",
                                        "json_schema": {"name": "outfit_items", "strict": True, "schema": SCHEMA}},
                },
            )
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        items = _clean(json.loads(r.json()["choices"][0]["message"]["content"]))
        if items:
            return items, "openai"
    except Exception as e:  # never include request details; type only
        log.warning("prompt parsing fell back: %s", type(e).__name__)
    return fallback_items(text), "fallback"
