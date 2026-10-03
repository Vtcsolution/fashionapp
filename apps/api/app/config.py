from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]  # project root (holds .env and storage/)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore", case_sensitive=False)

    cors_origins: str = "http://localhost:3000"

    # Fresh prototype uses its own SQLite DB; the shared DATABASE_URL in .env is ignored on purpose.
    vto_database_url: str = f"sqlite:///{(ROOT / 'storage' / 'vto.db').as_posix()}"
    storage_dir: Path = ROOT / "storage"

    # Retailers
    ebay_client_id: str = ""
    ebay_client_secret: str = ""
    ali_express_app_key: str = ""
    ali_express_secret_api: str = ""
    ali_express_tracking_id: str = ""
    cj_api_token: str = ""
    cj_company_id: str = ""  # publisher CID (not the website/PID); required by the CJ GraphQL API
    rakuten_enabled: bool = False
    rakuten_client_id: str = ""
    rakuten_client_secret: str = ""
    rakuten_account_id: str = ""

    # OpenAI / Gemini: prompt understanding and visual verification ONLY. Never image generation.
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1"
    gemini_api_key: str = ""
    # Text+vision model (alias that tracks the current Pro). Override with GEMINI_VISION_MODEL.
    # GEMINI_IMAGE_MODEL in .env is an image-GENERATION model and is deliberately NOT used.
    gemini_vision_model: str = "gemini-pro-latest"
    verification_mode: str = "auto"  # auto: LLM verification for real FASHN results, placeholder for mock | llm | placeholder

    # FASHN
    fashn_api_key: str = ""
    fashn_model: str = "tryon-max"
    # FASHN_LIVE_ENABLED / FASHN_LIVE_AUTHORIZATION are deliberately NOT settings: guard.py reads them
    # from the process environment only, so a value in .env can never enable a live call.
    fashn_credit_cap: int = 2

    # Image rules
    min_product_image_side: int = 512

    def configured(self) -> dict[str, bool]:
        return {
            "ebay": bool(self.ebay_client_id and self.ebay_client_secret),
            "aliexpress": bool(self.ali_express_app_key and self.ali_express_secret_api),
            "cj": bool(self.cj_api_token and self.cj_company_id),
            "rakuten": bool(self.rakuten_enabled and self.rakuten_client_id and self.rakuten_client_secret),
            "fashn_key": bool(self.fashn_api_key),
            "openai": bool(self.openai_api_key),
            "gemini": bool(self.gemini_api_key),
        }


settings = Settings()
