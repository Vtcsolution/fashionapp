from dataclasses import dataclass, field

CHECKS = ("product_presence", "product_correspondence", "color_details", "placement", "identity_preserved")
# Each check is PASS | FAIL | NOT_CHECKED. Overall is PASS | FAIL | REVIEW_REQUIRED. No percentages.


@dataclass
class Verification:
    checks: dict[str, str] = field(default_factory=lambda: {c: "NOT_CHECKED" for c in CHECKS})
    overall: str = "REVIEW_REQUIRED"
    notes: str = ""


def decide_overall(checks: dict[str, str]) -> str:
    """PASS only when every check is positively PASS. Any FAIL fails. Anything unconfirmed needs review."""
    values = [checks.get(c, "NOT_CHECKED") for c in CHECKS]
    if "FAIL" in values:
        return "FAIL"
    if all(v == "PASS" for v in values):
        return "PASS"
    return "REVIEW_REQUIRED"


class PlaceholderVerifier:
    """No computer vision yet, so it confirms nothing and says so. Slot for OpenAI/Gemini QC later."""

    async def verify(self, person: bytes, product: bytes, result: bytes) -> Verification:
        v = Verification(notes="No automated visual verification is implemented yet. A human must compare "
                               "the result with the source product image.")
        v.overall = decide_overall(v.checks)
        return v


def get_verifier() -> PlaceholderVerifier:
    return PlaceholderVerifier()
