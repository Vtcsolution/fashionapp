from dataclasses import dataclass


@dataclass
class Verification:
    product: str  # VERIFIED | UNVERIFIED
    identity: str  # PRESERVED | UNKNOWN | CHANGED
    overall: str  # PASS | REVIEW | FAIL
    notes: str = ""


def decide_overall(product: str, identity: str) -> str:
    """PASS only when both are positively confirmed; anything unconfirmed is REVIEW, never PASS."""
    if product == "VERIFIED" and identity == "PRESERVED":
        return "PASS"
    if identity == "CHANGED":
        return "FAIL"
    return "REVIEW"


class MockVerifier:
    """Placeholder. It cannot confirm anything, so it honestly reports REVIEW. No fake scores."""

    async def verify(self, person: bytes, product: bytes, result: bytes) -> Verification:
        product_s, identity_s = "UNVERIFIED", "UNKNOWN"
        return Verification(product_s, identity_s, decide_overall(product_s, identity_s),
                            "Mock verifier: no visual comparison was performed. Manual review required.")


def get_verifier() -> MockVerifier:
    return MockVerifier()  # later: Gemini visual QC behind the same interface
