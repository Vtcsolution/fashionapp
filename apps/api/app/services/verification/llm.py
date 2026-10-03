"""Real visual verification with OpenAI and Gemini (vision/text only; they never generate or edit images).

Per step, each model independently judges the new FASHN result against: the original photo, the image the step
started from, and the exact retailer product image. Per check a model answers PASS | FAIL | UNCERTAIN.

Combination (conservative, no percentages):
  any model says FAIL                             -> FAIL
  BOTH required models say PASS                   -> PASS
  anything else (uncertain / outage / one model)  -> REVIEW_REQUIRED
A missing or failing model can therefore never produce PASS.

After the last step a separate whole-look check judges the FINAL image against every selected product.
"""
import asyncio
import base64
import io
import json
import logging

import httpx
from PIL import Image

from ...config import settings
from .base import (
    REQUIRED_MODELS, FinalContext, Verification, VerifyContext, checks_for_step, decide_overall,
)

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


def build_final_prompt(ctx: FinalContext) -> str:
    n = len(ctx.products)
    listing = "\n".join(f"Image {i + 2}: product {i + 1} - \"{name[:100]}\" (category: {cat})"
                        for i, (name, cat, _) in enumerate(ctx.products))
    return (
        "You are a strict quality inspector for a virtual try-on system. The shopper selected several real products "
        "and they were applied one after another. Judge the FINAL image.\n"
        f"Image 1: the shopper's ORIGINAL photo.\n{listing}\n"
        f"Image {n + 2}: the FINAL try-on image.\n\n"
        "For EACH product 1..N say whether it is visibly present in the final image ('present') and whether it is the "
        "SAME item - same color, design and distinctive details, not a similar or generic one ('matches'). "
        "A missing item, a duplicated item or a changed color must not pass. Also say whether the person in the final "
        "image is the same person as in image 1 ('identity_preserved').\n"
        "Verdicts: PASS, FAIL (clearly wrong or missing), UNCERTAIN (cannot tell). Reasons max 25 words. "
        "Never give percentages.\n"
        'Reply with JSON only: {"identity_preserved": {"verdict": "...", "reason": "..."}, "items": '
        '[{"index": 1, "present": {"verdict": "...", "reason": "..."}, "matches": {"verdict": "...", "reason": "..."}}, ...]} '
        f"with exactly {n} items, index 1..{n} in the order listed."
    )


def _verdict(item) -> dict:
    v = str((item or {}).get("verdict", "")).upper()
    return {"verdict": v if v in VERDICTS else "UNCERTAIN", "reason": str((item or {}).get("reason", ""))[:200]}


def _normalize(raw: dict, checks: tuple[str, ...]) -> dict[str, dict]:
    return {c: _verdict(raw.get(c) if isinstance(raw, dict) else None) for c in checks}


def _normalize_final(raw: dict, n: int) -> dict:
    items = {int(i.get("index", 0)): i for i in (raw.get("items") or []) if isinstance(i, dict)} if isinstance(raw, dict) else {}
    return {
        "identity_preserved": _verdict(raw.get("identity_preserved") if isinstance(raw, dict) else None),
        "items": [{"present": _verdict(items.get(k, {}).get("present")), "matches": _verdict(items.get(k, {}).get("matches"))}
                  for k in range(1, n + 1)],
    }


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):] if "{" in text else text
    return json.loads(text[text.find("{"): text.rfind("}") + 1])


def _jpegs(*blobs: bytes) -> list[bytes]:
    return [prepare(b) for b in blobs]


def _verdict_schema():
    return {"type": "object", "additionalProperties": False, "required": ["verdict", "reason"],
            "properties": {"verdict": {"type": "string", "enum": list(VERDICTS)}, "reason": {"type": "string"}}}


class OpenAIJudge:
    name = "openai"

    def __init__(self, transport=None):
        self.transport = transport

    def enabled(self) -> bool:
        return bool(settings.openai_api_key)

    async def _ask(self, prompt: str, images: list[bytes], schema: dict, schema_name: str) -> dict:
        content = [{"type": "text", "text": prompt}]
        for img in images:
            content.append({"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64," + base64.b64encode(img).decode(), "detail": "high"}})
        async with httpx.AsyncClient(timeout=180, transport=self.transport) as c:
            r = await c.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                json={"model": settings.openai_model, "messages": [{"role": "user", "content": content}],
                      "response_format": {"type": "json_schema",
                                          "json_schema": {"name": schema_name, "strict": True, "schema": schema}}},
            )
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        return _parse_json(r.json()["choices"][0]["message"]["content"])

    async def judge(self, ctx: VerifyContext) -> dict[str, dict]:
        checks = checks_for_step(ctx.step)
        schema = {"type": "object", "additionalProperties": False, "required": list(checks),
                  "properties": {c: _verdict_schema() for c in checks}}
        raw = await self._ask(build_prompt(ctx), _jpegs(ctx.original, ctx.previous, ctx.product, ctx.result),
                              schema, "tryon_verification")
        return _normalize(raw, checks)

    async def judge_final(self, ctx: FinalContext) -> dict:
        item = {"type": "object", "additionalProperties": False, "required": ["index", "present", "matches"],
                "properties": {"index": {"type": "integer"}, "present": _verdict_schema(), "matches": _verdict_schema()}}
        schema = {"type": "object", "additionalProperties": False, "required": ["identity_preserved", "items"],
                  "properties": {"identity_preserved": _verdict_schema(), "items": {"type": "array", "items": item}}}
        imgs = _jpegs(ctx.original, *[p[2] for p in ctx.products], ctx.result)
        return _normalize_final(await self._ask(build_final_prompt(ctx), imgs, schema, "final_verification"),
                                len(ctx.products))


class GeminiJudge:
    name = "gemini"

    def __init__(self, transport=None):
        self.transport = transport

    def enabled(self) -> bool:
        return bool(settings.gemini_api_key)

    async def _ask(self, prompt: str, images: list[bytes]) -> dict:
        parts = [{"text": prompt}]
        for img in images:
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(img).decode()}})
        async with httpx.AsyncClient(timeout=240, transport=self.transport) as c:
            r = await c.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_vision_model}:generateContent",
                headers={"x-goog-api-key": settings.gemini_api_key},  # header, so the key never appears in a URL
                json={"contents": [{"role": "user", "parts": parts}],
                      "generationConfig": {"responseMimeType": "application/json"}},
            )
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        text = "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"])
        return _parse_json(text)

    async def judge(self, ctx: VerifyContext) -> dict[str, dict]:
        raw = await self._ask(build_prompt(ctx), _jpegs(ctx.original, ctx.previous, ctx.product, ctx.result))
        return _normalize(raw, checks_for_step(ctx.step))

    async def judge_final(self, ctx: FinalContext) -> dict:
        imgs = _jpegs(ctx.original, *[p[2] for p in ctx.products], ctx.result)
        return _normalize_final(await self._ask(build_final_prompt(ctx), imgs), len(ctx.products))


def _combine_verdicts(per_model: dict[str, str]) -> str:
    """per_model: model name -> verdict, for models that answered. Missing models count against PASS."""
    verdicts = list(per_model.values())
    if "FAIL" in verdicts:
        return "FAIL"
    if all(per_model.get(m) == "PASS" for m in REQUIRED_MODELS) and all(v == "PASS" for v in verdicts):
        return "PASS"
    return "REVIEW_REQUIRED"


def combine(ctx: VerifyContext, outcomes: dict[str, dict | Exception]) -> Verification:
    """outcomes: model name -> normalized verdicts, or the exception it raised."""
    answered = {n: o for n, o in outcomes.items() if not isinstance(o, Exception)}
    checks: dict[str, str] = {}
    notes: list[str] = []
    for c in checks_for_step(ctx.step):
        checks[c] = _combine_verdicts({n: o[c]["verdict"] for n, o in answered.items()})
        if checks[c] != "PASS":
            for name, o in answered.items():
                if o[c]["verdict"] != "PASS" and o[c]["reason"]:
                    notes.append(f"{c} [{name} {o[c]['verdict']}]: {o[c]['reason']}")
    for name, o in outcomes.items():
        if isinstance(o, Exception):
            notes.append(f"{name} unavailable ({type(o).__name__}); cannot confirm without it")
    missing = [m for m in REQUIRED_MODELS if m not in outcomes]
    if missing:
        notes.append(f"{', '.join(missing)} not configured; cannot confirm without it")
    models = {n: ({"error": type(o).__name__} if isinstance(o, Exception) else o) for n, o in outcomes.items()}
    return Verification(checks, decide_overall(checks), " | ".join(notes)[:900], models, sorted(answered))


def combine_final(ctx: FinalContext, outcomes: dict[str, dict | Exception]) -> dict:
    answered = {n: o for n, o in outcomes.items() if not isinstance(o, Exception)}
    notes: list[str] = []
    identity = _combine_verdicts({n: o["identity_preserved"]["verdict"] for n, o in answered.items()})
    items = []
    for k, (name, cat, _) in enumerate(ctx.products):
        present = _combine_verdicts({n: o["items"][k]["present"]["verdict"] for n, o in answered.items()})
        matches = _combine_verdicts({n: o["items"][k]["matches"]["verdict"] for n, o in answered.items()})
        items.append({"index": k + 1, "name": name, "category": cat, "present": present, "matches": matches})
        for n, o in answered.items():
            for what in ("present", "matches"):
                v = o["items"][k][what]
                if v["verdict"] != "PASS" and v["reason"]:
                    notes.append(f"product {k + 1} {what} [{n} {v['verdict']}]: {v['reason']}")
    for n, o in answered.items():
        v = o["identity_preserved"]
        if v["verdict"] != "PASS" and v["reason"]:
            notes.append(f"identity [{n} {v['verdict']}]: {v['reason']}")
    for n, o in outcomes.items():
        if isinstance(o, Exception):
            notes.append(f"{n} unavailable ({type(o).__name__}); cannot confirm without it")
    missing = [m for m in REQUIRED_MODELS if m not in outcomes]
    if missing:
        notes.append(f"{', '.join(missing)} not configured; cannot confirm without it")
    flat = [identity] + [x for i in items for x in (i["present"], i["matches"])]
    overall = "FAIL" if "FAIL" in flat else "PASS" if all(v == "PASS" for v in flat) else "REVIEW_REQUIRED"
    return {"overall": overall, "identity_preserved": identity, "items": items, "notes": " | ".join(notes)[:1200],
            "performed_by": sorted(answered),
            "models": {n: ({"error": type(o).__name__} if isinstance(o, Exception) else o) for n, o in outcomes.items()}}


class LlmVerifier:
    def __init__(self, judges=None):
        self.judges = judges  # tests inject fakes; production uses whichever configured models exist

    def _judges(self):
        return self.judges if self.judges is not None else [j for j in (OpenAIJudge(), GeminiJudge()) if j.enabled()]

    async def verify(self, ctx: VerifyContext) -> Verification:
        judges = self._judges()
        if not judges:
            checks = {c: "NOT_CHECKED" for c in checks_for_step(ctx.step)}
            return Verification(checks, "REVIEW_REQUIRED", "NOT VERIFIED: no vision model configured.", {}, [])
        results = await asyncio.gather(*(j.judge(ctx) for j in judges), return_exceptions=True)
        for j, res in zip(judges, results):
            if isinstance(res, Exception):
                log.warning("verification model %s failed: %s", j.name, type(res).__name__)
        return combine(ctx, {j.name: r for j, r in zip(judges, results)})

    async def verify_final(self, ctx: FinalContext) -> dict | None:
        judges = self._judges()
        if not judges:
            return None
        results = await asyncio.gather(*(j.judge_final(ctx) for j in judges), return_exceptions=True)
        for j, res in zip(judges, results):
            if isinstance(res, Exception):
                log.warning("final verification model %s failed: %s", j.name, type(res).__name__)
        return combine_final(ctx, {j.name: r for j, r in zip(judges, results)})
