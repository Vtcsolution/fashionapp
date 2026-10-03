import base64

# First live test configuration (fixed on purpose; not user-configurable).
FIRST_TEST_CONFIG = {
    "resolution": "1k",
    "generation_mode": "balanced",
    "num_images": 1,
    "output_format": "png",
}
CREDITS_PER_GENERATION = 2


def data_uri(data: bytes, ext: str) -> str:
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}[ext]
    return f"data:image/{mime};base64,{base64.b64encode(data).decode()}"


def build_request(model_name: str, person: bytes, person_ext: str, product: bytes, product_ext: str) -> dict:
    """Build the FASHN /v1/run payload. Pure function: no network, no secrets."""
    return {
        "model_name": model_name,
        "inputs": {
            "model_image": data_uri(person, person_ext),
            "product_image": data_uri(product, product_ext),
            **FIRST_TEST_CONFIG,
        },
    }


def redact(payload: dict) -> dict:
    """Loggable copy: image payloads replaced by their length."""
    inputs = {k: (f"<{len(v)} chars>" if isinstance(v, str) and v.startswith("data:") else v)
              for k, v in payload["inputs"].items()}
    return {**payload, "inputs": inputs}
