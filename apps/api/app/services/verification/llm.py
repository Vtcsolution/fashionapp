"""Real visual verification with OpenAI and Gemini (vision/text only; they never generate or edit images).

Each model independently judges the new FASHN result against: the original photo, the image the step started
from, and the exact retailer product image. Per check a model answers PASS | FAIL | UNCERTAIN.

Combination (conservative, no percentages):
  any model says FAIL                   -> FAIL
  every configured model says PASS      -> PASS
  anything else (uncertain / API error) -> REVIEW_REQUIRED
A missing or failing model can therefore never produce PASS.
"""
import asyncio
import base64
import io
import json
import logging

import httpx
from PIL import Image

from ...config import settings
from .base import Verification, VerifyContext, checks_for_step, decide_overall

log = logging.getLogger(__name__)
VERDICTS = ("PASS", "FAIL", "UNCERTAIN")

CHECK_QUESTIONS = {
    "product_presence": "Is the item from image 3 (the product) visibly worn or carried in image 4?",
    "product_correspondence": "Is it the SAME item as image 3, not a similar, generic or substituted one?",
    "color": "Does its color match image 3 (ignoring ordinary lighting differences)?",
    "details": "Do its pattern, texture, graphics, logos, distinctive details and proportions match image 3?",
    "placement": "Is it placed correctly on the body (shoes on feet, bag held or on shoulder, sunglasses on face, "
                 "garment worn properly), with no duplicate copies and nothing floating or fused?",
    "identity_preserved": "Is the person in image 4 the same person as image 1 (face, hair, skin, body proportions, hands)?",
    "earlier_items_preserved": "Is every clothing item and accessory visible in image 2 still present and unchanged "
                               "(same color/design) in image 4? Nothing may have disappeared, duplicated or changed, "
                               "except what the new item necessarily replaces.",
}


def prepare(data: bytes, max_side: int = 1280) -> bytes:
    """JPEG copy for the vision API only (cost/size). The stored FASHN result is never touched."""
    im = Image.open(io.BytesIO(data)).convert("RGB")
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    return buf.getvalue()


def build_prompt(ctx: VerifyContext) -> str:
    checks = checks_for_step(ctx.step)
    qs = "\n".join(f"- {c}: {CHECK_QUESTIONS[c]}" for c in checks)
    return (
        "You are a strict quality inspector for a virtual try-on system. You will see 4 images in this order:\n"
        "Image 1: the shopper's ORIGINAL photo (identity reference).\n"
        "Image 2: the BASE image given to this try-on step (before the new item)"
        + (" - identical to image 1 for the first step.\n" if ctx.step == 1 else ".\n")
        + f"Image 3: the retailer PRODUCT photo of the new item: \"{ctx.product_name[:120]}\" (category: {ctx.category}).\n"
        "Image 4: the try-on RESULT to judge.\n\n"
        "Answer each check with a verdict PASS, FAIL or UNCERTAIN and a short reason (max 25 words).\n"
        "Use FAIL only when the problem is clear. Use UNCERTAIN when you cannot tell. Never give percentages.\n"
        f"{qs}\n"
        'Reply with JSON only: {"<check>": {"verdict": "...", "reason": "..."}, ...} using exactly these keys: '
        + ", ".join(checks) + "."
    )


def _normalize(raw: dict, checks: tuple[str, ...]) -> dict[str, dict]:
    out = {}
    for c in checks:
        item = raw.get(c) if isinstance(raw, dict) else None
        verdict = str((item or {}).get("verdict", "")).upper()
        out[c] = {"verdict": verdict if verdict in VERDICTS else "UNCERTAIN",
                  "reason": str((item or {}).get("reason", ""))[:200]}
    return out


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):] if "{" in text else text
    return json.loads(text[text.find("{"): text.rfind("}") + 1])


def _images(ctx: VerifyContext) -> list[bytes]:
    return [prepare(b) for b in (ctx.original, ctx.previous, ctx.product, ctx.result)]


class OpenAIJudge:
    name = "openai"

    def __init__(self, transport=None):
        self.transport = transport

    def enabled(self) -> bool:
        return bool(settings.openai_api_key)

    async def judge(self, ctx: VerifyContext) -> dict[str, dict]:
        checks = checks_for_step(ctx.step)
        content = [{"type": "text", "text": build_prompt(ctx)}]
        for img in _images(ctx):
            content.append({"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64," + base64.b64encode(img).decode(), "detail": "high"}})
        item = {"type": "object", "additionalProperties": False, "required": ["verdict", "reason"],
                "properties": {"verdict": {"type": "string", "enum": list(VERDICTS)}, "reason": {"type": "string"}}}
        schema = {"type": "object", "additionalProperties": False, "required": list(checks),
                  "properties": {c: item for c in checks}}
        async with httpx.AsyncClient(timeout=120, transport=self.transport) as c:
            r = await c.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                json={"model": settings.openai_model, "messages": [{"role": "user", "content": content}],
                      "response_format": {"type": "json_schema",
                                          "json_schema": {"name": "tryon_verification", "strict": True, "schema": schema}}},
            )
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        return _normalize(_parse_json(r.json()["choices"][0]["message"]["content"]), checks)


class GeminiJudge:
    name = "gemini"

    def __init__(self, transport=None):
        self.transport = transport

    def enabled(self) -> bool:
        return bool(settings.gemini_api_key)

    async def judge(self, ctx: VerifyContext) -> dict[str, dict]:
        checks = checks_for_step(ctx.step)
        parts = [{"text": build_prompt(ctx)}]
        for img in _images(ctx):
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(img).decode()}})
        async with httpx.AsyncClient(timeout=180, transport=self.transport) as c:
            r = await c.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_vision_model}:generateContent",
                headers={"x-goog-api-key": settings.gemini_api_key},  # header, so the key never appears in a URL
                json={"contents": [{"role": "user", "parts": parts}],
                      "generationConfig": {"responseMimeType": "application/json"}},
            )
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        text = "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"])
        return _normalize(_parse_json(text), checks)


def combine(ctx: VerifyContext, outcomes: dict[str, dict | Exception]) -> Verification:
    """outcomes: model name -> normalized verdicts, or the exception it raised."""
    checks_list = checks_for_step(ctx.step)
    n_models = len(outcomes)
    checks: dict[str, str] = {}
    notes: list[str] = []
    for c in checks_list:
        verdicts = [o[c]["verdict"] for o in outcomes.values() if not isinstance(o, Exception)]
        if "FAIL" in verdicts:
            checks[c] = "FAIL"
        elif n_models and verdicts.count("PASS") == n_models:
            checks[c] = "PASS"
        else:
            checks[c] = "REVIEW_REQUIRED"
        if checks[c] != "PASS":
            for name, o in outcomes.items():
                if not isinstance(o, Exception) and o[c]["verdict"] != "PASS" and o[c]["reason"]:
                    notes.append(f"{c} [{name} {o[c]['verdict']}]: {o[c]['reason']}")
    for name, o in outcomes.items():
        if isinstance(o, Exception):
            notes.append(f"{name} unavailable ({type(o).__name__}); cannot confirm without it")
    models = {n: ({"error": type(o).__name__} if isinstance(o, Exception) else o) for n, o in outcomes.items()}
    return Verification(checks, decide_overall(checks), " | ".join(notes)[:900], models)


class LlmVerifier:
    def __init__(self, judges=None):
        self.judges = judges  # tests inject fakes; production uses whichever configured models exist

    async def verify(self, ctx: VerifyContext) -> Verification:
        judges = self.judges if self.judges is not None else [j for j in (OpenAIJudge(), GeminiJudge()) if j.enabled()]
        if not judges:
            checks = {c: "NOT_CHECKED" for c in checks_for_step(ctx.step)}
            return Verification(checks, "REVIEW_REQUIRED", "No vision model configured; human review required.")
        results = await asyncio.gather(*(j.judge(ctx) for j in judges), return_exceptions=True)
        for j, res in zip(judges, results):
            if isinstance(res, Exception):
                log.warning("verification model %s failed: %s", j.name, type(res).__name__)
        return combine(ctx, {j.name: r for j, r in zip(judges, results)})
