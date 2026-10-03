from dataclasses import dataclass, field

from ...config import settings

# Each check is PASS | FAIL | REVIEW_REQUIRED (uncertain) | NOT_CHECKED. Overall: PASS | FAIL | REVIEW_REQUIRED.
# No percentages anywhere.
CHECKS = ("product_presence", "product_correspondence", "color", "details", "placement", "identity_preserved")
# From step 2 on we also check that nothing from earlier steps disappeared, duplicated or changed color.
EXTRA_CHECKS = ("earlier_items_preserved",)
# A PASS needs BOTH of these to have actually analysed the images. One model alone can never confirm anything.
REQUIRED_MODELS = ("openai", "gemini")


def checks_for_step(step: int) -> tuple[str, ...]:
    return CHECKS + (EXTRA_CHECKS if step > 1 else ())


@dataclass
class VerifyContext:
    step: int
    product_name: str
    category: str
    original: bytes  # the user's original photo (identity reference)
    previous: bytes  # the image given to this FASHN step (the original for step 1)
    product: bytes  # the exact retailer product image that was sent
    result: bytes  # the raw FASHN output being judged


@dataclass
class FinalContext:
    """Whole-look check: the FINAL image against EVERY selected product and the original photo."""

    original: bytes
    result: bytes
    products: list[tuple[str, str, bytes]]  # (name, category, retailer image) in application order


@dataclass
class Verification:
    checks: dict[str, str]
    overall: str = "REVIEW_REQUIRED"
    notes: str = ""
    models: dict = field(default_factory=dict)  # per-model raw verdicts + reasons, for the audit trail
    performed_by: list[str] = field(default_factory=list)  # models that really analysed the images; [] = nobody


def decide_overall(checks: dict[str, str]) -> str:
    """PASS only when every check is positively PASS. Any FAIL fails. Anything unconfirmed needs review."""
    values = list(checks.values())
    if "FAIL" in values:
        return "FAIL"
    if values and all(v == "PASS" for v in values):
        return "PASS"
    return "REVIEW_REQUIRED"


class PlaceholderVerifier:
    """Confirms nothing and says so. Used for mock results, where a vision check would be meaningless."""

    async def verify(self, ctx: VerifyContext) -> Verification:
        checks = {c: "NOT_CHECKED" for c in checks_for_step(ctx.step)}
        return Verification(checks, "REVIEW_REQUIRED",
                            "NOT VERIFIED: no visual analysis was performed (mock/demo result).", {}, [])

    async def verify_final(self, ctx: FinalContext) -> dict | None:
        return None  # nothing was analysed, so there is no final verification to report


def vision_models_configured() -> bool:
    return bool(settings.openai_api_key and settings.gemini_api_key)


def get_verifier(provider: str):
    """auto: real OpenAI+Gemini vision for real FASHN output, placeholder for mock output."""
    from .llm import LlmVerifier  # local import: llm.py imports this module

    mode = settings.verification_mode
    if mode == "placeholder" or (mode == "auto" and provider == "mock"):
        return PlaceholderVerifier()
    return LlmVerifier()
